"""Checks for the review and dogfood fixes: request guard, participant ids,
memory, audit chain, sensor boundary, calorie plausibility, guideline
retrieval, signal reviews, restored transcripts and readable outputs.

No model calls. Run: ../.venv/bin/python test_hardening.py
"""
import datetime as dt
import json
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import anyio

import agent
import audit
import evidence
import monitor
import observe
import watch
import workspace
import results
import realtime
import server
from profile import Profile

# Conversations persist per participant; never read or overwrite the real ones.
agent.SESSIONS_DIR = Path(tempfile.mkdtemp())
workspace.ROOT = Path(tempfile.mkdtemp())


def check(name, condition):
    assert condition, name
    print("PASS", name)


def audit_chain_and_archive():
    with tempfile.TemporaryDirectory() as temp:
        previous, audit.LOG_DIR = audit.LOG_DIR, Path(temp)
        try:
            for text in ("one", "two", "three"):
                audit.append(audit.from_turn("p16", text, {"reply": text}))
            clean = audit.verify("p16")
            lines = audit.path("p16").read_text().splitlines()
            lines[0] = lines[0].replace('"output": "one"', '"output": "edited"')
            audit.path("p16").write_text("\n".join(lines) + "\n")
            tampered = audit.verify("p16")
            moved = audit.archive("p16")
        finally:
            audit.LOG_DIR = previous
        check("an edited earlier audit entry breaks the hash chain",
              clean == [] and any("hash chain" in x for x in tampered))
        check("reset archives the audit log instead of deleting it",
              moved is not None and moved.parent.name == "archive")


def sensor_boundary_and_calories():
    s = agent.Session("p12", Profile("p12"), clock=dt.date(2019, 11, 12))
    s.as_of, s.as_at = dt.date(2019, 11, 11), dt.datetime(2019, 11, 12, 8, 20)
    tools = {t.name: t for t in agent.build_tools(s)}
    args = {"start": "2019-11-12T07:30:00", "end": "2019-11-12T08:20:00", "bucket_minutes": 5}
    anyio.run(tools["inspect_sensor_window"].handler, args)
    cached = realtime.sensor_window("p12", dt.datetime(2019, 11, 12, 7, 30),
                                    dt.datetime(2019, 11, 12, 8, 20), 5)
    check("the cached sensor window is never mutated by the tool",
          "logged_exercise" not in cached)
    check("implausible calorie readings are flagged, not read as effort",
          cached["summary"]["implausible_calorie_buckets"] > 0
          and any("device artefact" in x for x in cached["limitations"]))

    running = next(x for x in s.profile.sessions if x.start and x.minutes >= 20)
    cut = running.start + dt.timedelta(minutes=5)
    s.as_of, s.as_at = running.start.date(), cut
    out = json.loads(anyio.run(tools["inspect_sensor_window"].handler, {
        "start": (cut - dt.timedelta(minutes=30)).isoformat(),
        "end": cut.isoformat()})["content"][0]["text"])
    live = [x for x in out["logged_exercise"] if x["start"] == running.start.isoformat(sep=" ")]
    check("a session still running at the event time exposes no future totals",
          live and "minutes" not in live[0] and "average_hr_bpm" not in live[0])


DONE = {"type": "completed", "status": "completed", "error": None, "duration_ms": 1}


def final(answer, outcome="answer", reason="test"):
    """The final answer message a model turn ends with."""
    return {"type": "message", "phase": "final_answer",
            "text": json.dumps({"answer": answer, "outcome": outcome, "reason": reason})}


def usage(new, cached=0, output=10):
    return {"type": "usage", "last": {"input_tokens": new + cached, "cached_input_tokens": cached,
                                      "output_tokens": output, "reasoning_output_tokens": 0}}


def tokens_are_per_turn():
    s = agent.Session("p16", Profile("p16"), clock=dt.date(2020, 3, 1))

    class Client:
        fail = True

        async def send(self, prompt, schema=None):
            # The whole history is resent each request; most of it is cached.
            yield usage(new=400, cached=6000)
            yield usage(new=100, cached=6400)
            if not Client.fail:
                yield final("Hello.")
            yield ({**DONE, "status": "failed", "error": {"message": "usage limit"}}
                   if Client.fail else DONE)
    s._client = Client()
    with tempfile.TemporaryDirectory() as temp:
        previous, audit.LOG_DIR = audit.LOG_DIR, Path(temp)
        try:
            try:
                anyio.run(agent.turn, s, "hello")
            except RuntimeError:
                pass
            failed = audit.read("p16")[-1]
            Client.fail = False
            second = anyio.run(agent.turn, s, "again")
        finally:
            audit.LOG_DIR = previous
    check("a turn reports what it newly processed, not the resent cached history",
          failed["usage"]["new_input"] == 500 and failed["usage"]["cached_input"] == 12400
          and failed["usage"]["context"] == 6500 and failed["tokens"] == 520)
    check("a failed turn's usage never spills into the next turn",
          second["tokens"] == 520 and second["turns"] == 2)


def request_cap():
    s = agent.Session("p16", Profile("p16"), clock=dt.date(2020, 3, 1))

    class Finished:
        """Runs past the cap but ends by itself, as a long plan turn can."""
        async def send(self, prompt, schema=None):
            for _ in range(agent.MAX_MODEL_REQUESTS + 1):
                yield usage(new=10)
            yield final("The record is not enough to judge.")
            yield DONE

        async def interrupt(self):
            pass

    class Runaway(Finished):
        async def send(self, prompt, schema=None):
            for _ in range(agent.MAX_MODEL_REQUESTS + 1):
                yield usage(new=10)
            yield {**DONE, "status": "interrupted"}

        async def interrupt(self):
            Runaway.interrupted = True

    with tempfile.TemporaryDirectory() as temp:
        previous, audit.LOG_DIR = audit.LOG_DIR, Path(temp)
        try:
            s._client = Finished()
            kept = anyio.run(agent.turn, s, "what does my recovery look like?")
            s._client = Runaway()
            try:
                anyio.run(agent.turn, s, "loop forever")
                stopped = None
            except RuntimeError as exc:
                stopped = str(exc)
        finally:
            audit.LOG_DIR = previous
    check("a turn that finishes by itself past the request cap keeps its answer",
          kept["summary_status"] == "accepted" and kept["execution"]["status"] == "completed")
    check("a runaway turn is interrupted at the request cap and says why",
          getattr(Runaway, "interrupted", False) and stopped
          and f"{agent.MAX_MODEL_REQUESTS} model requests" in stopped)

    from openai_codex.errors import InvalidRequestError
    conversation = agent.CodexConversation(agent.MODEL, "test")
    conversation.thread_id, conversation.turn_id = "thread", "turn"

    def already_over(*args):
        raise InvalidRequestError(-32600, "no active turn to interrupt")
    conversation.codex.turn_interrupt = already_over
    try:
        anyio.run(conversation.interrupt)
        tolerated = True
    except InvalidRequestError:
        tolerated = False
    finally:
        conversation.close()
    check("interrupting a turn that has just ended is not an error", tolerated)


def failed_turn_commits_nothing():
    s = agent.Session("p16", Profile("p16"), clock=dt.date(2020, 3, 1))

    class Client:
        workspace = str(Path(tempfile.mkdtemp()) / "workspace")

        async def send(self, prompt, schema=None):
            with open(Path(self.workspace) / workspace.MEMORY, "a") as f:
                f.write(workspace.entry_line("2020-03-01", "preference", "dislikes swimming",
                                             "I hate swimming") + "\n")
            yield {**DONE, "status": "failed", "error": {"message": "usage limit"}}
    s._client = Client()
    with tempfile.TemporaryDirectory() as temp:
        previous = audit.LOG_DIR
        audit.LOG_DIR = Path(temp)
        try:
            try:
                anyio.run(agent.turn, s, "I hate swimming")
            except RuntimeError:
                pass
            row = audit.read("p16")[-1]
        finally:
            audit.LOG_DIR = previous
    check("a failed turn is marked failed in the audit log, not dressed as a reply",
          row["status"] == "failed" and "usage limit" in row["output"])
    check("a failed turn commits no memory", workspace.memory(s.workspace_home) == [])


def guideline_retrieval():
    s = agent.Session("p16", Profile("p16"), clock=dt.date(2020, 3, 1))
    s.evidence_token = "t1"
    tools = {t.name: t for t in agent.build_tools(s)}
    out = json.loads(anyio.run(tools["search_guidelines"].handler,
                               {"query": "how much sleep do adults need"})["content"][0]["text"])
    top = out["passages"][0]
    cards = [c for c in out["evidence_cards"] if c["kind"] == "knowledge"]
    check("guideline search returns cited passages as knowledge cards, tagged in place",
          top["id"].startswith("sleep-duration") and top["url"].startswith("https://")
          and cards and top["card_id"] == cards[0]["id"] and "statement" not in cards[0]
          and s.evidence_cards[cards[0]["id"]]["url"] == top["url"])
    guidance = {"answer": f"Adults are advised to sleep enough every night [{cards[0]['id']}].",
                "outcome": "answer", "reason": "general guidance"}
    check("with no personal records read, citing guidance alone is fine",
          evidence.validate(guidance, s.evidence_cards) == [])
    anyio.run(tools["read_record"].handler, {"days": 14})
    check("once their records were read, guidance alone cannot answer about this person",
          any("cited none of them" in e for e in evidence.validate(guidance, s.evidence_cards)))
    call = {"name": "search_guidelines", "input": {"query": "sleep"},
            "result": {"content": [{"type": "text", "text": json.dumps(out)}]}}
    section = next(x for x in results.build_sections([call], None) if x["title"] == "Guidance retrieved")
    check("retrieved guidance is displayed with its source and citation",
          section["rows"][0][1] == top["text"] and top["citation"] in section["notes"][0])
    none = json.loads(anyio.run(tools["search_guidelines"].handler,
                                {"query": "zzzz quantum banana"})["content"][0]["text"])
    check("no match says so instead of inviting an answer from memory",
          none["passages"] == [] and "No passage" in none["note"])


def one_review_per_signal():
    with tempfile.TemporaryDirectory() as temp:
        previous, watch.STATE_DIR = watch.STATE_DIR, Path(temp)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            p = Profile("p16")
            day = dt.date(2019, 11, 22)
            sig = next(x for x in monitor.signals_on(p.snapshot(day), day) if x.kind == "resting_hr")
            watch.extend_run("p16", dt.date(2019, 11, 20), day)
            watch.record_review("p16", {"notify": True, "reason": "r", "verdict": "unexplained"}, sig)
            state = watch.run_state("p16")
            body = json.dumps({"pid": "p16", "turn": "again", "as_of": "2020-03-31",
                               "day": "2019-11-22", "kind": "resting_hr"}).encode()
            req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/api/signal", body,
                                         {"Content-Type": "application/json"})
            try:
                urllib.request.urlopen(req, timeout=30)
                code = 200
            except urllib.error.HTTPError as exc:
                code = exc.code
        finally:
            httpd.shutdown()
            watch.STATE_DIR = previous
    check("a reviewed signal is recorded once and cannot be reviewed again in the run",
          len(state["alerts"]) == 1 and watch.signal_key(sig) in state["reviewed"] and code == 409)


def transcript_survives_the_page():
    s = agent.Session("p16", Profile("p16"), clock=dt.date(2020, 3, 1))

    class Client:
        async def send(self, prompt, schema=None):
            yield final("Hello.")
            yield DONE
    s._client = Client()
    with tempfile.TemporaryDirectory() as temp:
        previous, audit.LOG_DIR = audit.LOG_DIR, Path(temp)
        try:
            anyio.run(agent.turn, s, "hello coach")
        finally:
            audit.LOG_DIR = previous
    check("each finished turn is kept for a reload or athlete switch",
          len(s.transcript) == 1 and s.transcript[0]["message"] == "hello coach"
          and s.transcript[0]["reply"])


def readable_outputs():
    line, _ = observe._delta_summary("sleep", {"median_hours": 5.96}, {"median_hours": 6.42})
    check("a period comparison names the measure and carries its unit",
          line == "Median sleep: 5.96 h this period against 6.42 h in the reference, 0.46 h lower.")
    groups = server._parse_suite("PASS one\n", "Traceback\nAssertionError: two broke", 1)
    check("a suite that stops on an assertion is reported as a failure",
          [c["ok"] for g in groups for c in g["checks"]] == [True, False])


def turn_time_budget():
    s = agent.Session("p16", Profile("p16"), clock=dt.date(2020, 3, 1))
    s.transcript = [{"kind": "user", "message": "earlier"}]

    class SlowClient:
        disconnected = False

        async def send(self, prompt, schema=None):
            await anyio.sleep(5)
            yield DONE

        def close(self):
            SlowClient.disconnected = True
    s._client = SlowClient()
    previous_budget, agent.TURN_TIMEOUT_S = agent.TURN_TIMEOUT_S, 0.2
    with tempfile.TemporaryDirectory() as temp:
        previous, audit.LOG_DIR = audit.LOG_DIR, Path(temp)
        try:
            try:
                anyio.run(agent.turn, s, "slow")
                stopped = False
            except RuntimeError as exc:
                stopped = "longer than" in str(exc)
            row = audit.read("p16")[-1]
        finally:
            audit.LOG_DIR = previous
            agent.TURN_TIMEOUT_S = previous_budget
    check("a turn over its time budget is stopped, audited as failed, and the conversation restarts",
          stopped and row["execution"]["status"] == "failed" and SlowClient.disconnected
          and s._client is None and s.transcript == [])


def final_answer_corrections():
    s = agent.Session("p16", Profile("p16"), clock=dt.date(2020, 3, 1))
    sent = []

    class Corrected:
        """Cites an ID that does not exist, then fixes it when told."""
        async def send(self, prompt, schema=None):
            sent.append(prompt)
            if len(sent) == 1:
                yield final("Your sleep looks fine [00000000-e99].")
            else:
                yield final("I cannot tell from these records yet.")
            yield DONE

    class Stubborn:
        async def send(self, prompt, schema=None):
            sent.append(prompt)
            yield final("Your sleep looks fine [00000000-e99].")
            yield DONE

    with tempfile.TemporaryDirectory() as temp:
        previous, audit.LOG_DIR = audit.LOG_DIR, Path(temp)
        try:
            s._client = Corrected()
            fixed = anyio.run(agent.turn, s, "how is my sleep?")
            sent.clear()
            s._client = Stubborn()
            try:
                anyio.run(agent.turn, s, "how is my sleep now?")
                refused = None
            except RuntimeError as exc:
                refused = str(exc)
            row = audit.read("p16")[-1]
        finally:
            audit.LOG_DIR = previous
    check("an invented citation goes back to the same session with the exact error",
          fixed["summary_status"] == "accepted" and fixed["reply"] == "I cannot tell from these records yet."
          and "00000000-e99" in fixed["execution"]["corrections"][0]["errors"][0]
          and any(t.startswith("final answer returned for correction") for t in fixed["trace"]))
    check("a final answer still wrong after two corrections fails the turn and shows nothing",
          len(sent) == 1 + agent.MAX_CORRECTIONS and refused and "after 2 corrections" in refused
          and row["status"] == "failed" and row["stop"] == "failed")


def borrowed_chatgpt_login():
    import base64
    import time

    def jwt(exp):
        claims = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
        return f"h.{claims}.s"
    live, dead = jwt(time.time() + 3600), jwt(time.time() - 60)
    saved = agent.PI_ACCOUNTS, agent.CODEX_AUTH
    with tempfile.TemporaryDirectory() as temp:
        agent.PI_ACCOUNTS, agent.CODEX_AUTH = Path(temp) / "pi.json", Path(temp) / "auth.json"
        agent.PI_ACCOUNTS.write_text(json.dumps({"activeId": "a", "chainOrder": ["a", "b"], "accounts": [
            {"id": "a", "credential": {"type": "oauth", "access": dead, "accountId": "pi-a"}},
            {"id": "b", "credential": {"type": "oauth", "access": live, "accountId": "pi-b"}}]}))
        agent.PI_ACCOUNTS.chmod(0o600)
        agent.CODEX_AUTH.write_text(json.dumps({"tokens": {"access_token": live, "account_id": "codex",
                                                           "refresh_token": "never-used"}}))
        try:
            found = agent.chatgpt_credentials()
            check("borrowed logins skip expired tokens and keep Pi before ~/.codex",
                  [c["account_id"] for c in found] == ["pi-b", "codex"])
            check("only the access token is borrowed, never the refresh token",
                  all(set(c) == {"access", "account_id"} for c in found))
            agent.PI_ACCOUNTS.chmod(0o644)
            check("a Pi account store readable by others is ignored",
                  [c["account_id"] for c in agent.chatgpt_credentials()] == ["codex"])
        finally:
            agent.PI_ACCOUNTS, agent.CODEX_AUTH = saved


def conversation_persists_per_participant():
    saved = agent.SESSIONS_DIR, audit.LOG_DIR, server.SESSIONS, agent.CodexConversation
    started = []

    class FakeConversation:
        def __init__(self, model, instructions, tools, run_tool=None, on_denied=None,
                     home=None, resume=None, **skills):
            self.home, self.resume, self.run_tool = home, resume, run_tool
            self.thread_id = resume or f"thread-{len(started) + 1}"
            started.append(self)

        def start(self):
            pass

        def close(self):
            pass

        async def send(self, prompt, schema=None):
            yield final("Noted.")
            yield DONE

    with tempfile.TemporaryDirectory() as temp:
        agent.SESSIONS_DIR, audit.LOG_DIR = Path(temp) / "sessions", Path(temp) / "audit"
        agent.CodexConversation = FakeConversation
        try:
            p05 = agent.Session("p05", Profile("p05"), clock=dt.date(2020, 3, 1))
            anyio.run(agent.turn, p05, "hello from p05")
            p16 = agent.Session("p16", Profile("p16"), clock=dt.date(2020, 2, 1))
            anyio.run(agent.turn, p16, "hello from p16")
            check("each participant gets their own thread in their own directory",
                  started[0].home == agent.SESSIONS_DIR / "p05" / "codex"
                  and started[1].home == agent.SESSIONS_DIR / "p16" / "codex"
                  and p05.thread_id != p16.thread_id)

            # A server restart: fresh objects, state read back from disk.
            back = agent.Session("p05", Profile("p05"), clock=dt.date(2020, 3, 31)).load()
            check("after a restart the conversation, its messages and its clock come back",
                  back.thread_id == p05.thread_id and back.clock == dt.date(2020, 3, 1)
                  and [t["message"] for t in back.transcript] == ["hello from p05"]
                  and [m["text"] for m in back.user_messages] == ["hello from p05"])
            anyio.run(agent.turn, back, "still there?")
            check("the next message resumes that same Codex thread",
                  started[-1].resume == p05.thread_id and len(back.transcript) == 2)
            check("only the last turn keeps its trail, with timed steps, for a reload",
                  "steps" not in back.transcript[0] and back.transcript[1]["steps"]
                  and all(e.get("t") for e in back.transcript[1]["steps"])
                  and back.transcript[1]["steps"][0]["state"] == "receiving")
            try:
                back.set_clock(dt.date(2020, 2, 1))
                moved_back = True
            except ValueError:
                moved_back = False
            check("a restored conversation still refuses to move time backwards", not moved_back)
            stale = agent.Session("p05", Profile("p05"), clock=dt.date(2020, 3, 31)).load()
            stale._tools_digest = "an older tool catalogue"
            stale.set_clock(dt.date(2020, 2, 1))
            check("a conversation the next message cannot resume does not hold the clock",
                  stale.clock == dt.date(2020, 2, 1) and not stale.in_conversation)

            back._tools_digest = "an older tool catalogue"
            back._client = None
            anyio.run(agent.turn, back, "new tools")
            check("a changed tool catalogue starts a new thread and clears the stale transcript",
                  started[-1].resume is None and back.thread_id != p05.thread_id
                  and [t["message"] for t in back.transcript] == ["new tools"])

            server.SESSIONS = {}
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{httpd.server_port}"
            try:
                with urllib.request.urlopen(base + "/api/transcript?pid=p16") as r:
                    restored = json.load(r)
                check("the page gets a participant's conversation back after a restart",
                      [t["message"] for t in restored["turns"]] == ["hello from p16"]
                      and restored["clock"] == "2020-02-01")
                stale = json.loads((agent.SESSIONS_DIR / "p16" / "conversation.json").read_text())
                stale["tools_digest"] = "an older system prompt"
                (agent.SESSIONS_DIR / "p16" / "conversation.json").write_text(json.dumps(stale))
                server.SESSIONS = {}
                with urllib.request.urlopen(base + "/api/transcript?pid=p16") as r:
                    dropped = json.load(r)
                check("a conversation the next turn cannot resume is not shown as remembered",
                      dropped == {"turns": [], "clock": None})
                reset = urllib.request.Request(base + "/api/reset", json.dumps({"pid": "p05"}).encode(),
                                               {"Content-Type": "application/json"})
                urllib.request.urlopen(reset).read()
                check("Reset forgets only that participant's conversation",
                      not (agent.SESSIONS_DIR / "p05").exists()
                      and (agent.SESSIONS_DIR / "p16" / "conversation.json").exists())
            finally:
                httpd.shutdown()
        finally:
            agent.SESSIONS_DIR, audit.LOG_DIR, server.SESSIONS, agent.CodexConversation = saved


def state_dirs_are_created_on_first_write():
    saved = watch.STATE_DIR, audit.LOG_DIR, workspace.ROOT
    with tempfile.TemporaryDirectory() as temp:
        deep = Path(temp) / "not" / "yet"
        watch.STATE_DIR, audit.LOG_DIR = deep / "watch", deep / "audit"
        workspace.ROOT = deep / "workspace"
        try:
            watch.extend_run("p16", dt.date(2020, 2, 1), dt.date(2020, 2, 3))
            audit.append(audit.from_turn("p16", "hi", {"reply": "hello"}))
            staged = Path(temp) / "staged"
            workspace.stage(workspace.home("p16"), staged, dt.date(2020, 3, 1))
            workspace.commit(workspace.home("p16"), staged, dt.date(2020, 3, 1))
            made = all(d.is_dir() for d in (watch.STATE_DIR, audit.LOG_DIR, workspace.home("p16")))
        finally:
            watch.STATE_DIR, audit.LOG_DIR, workspace.ROOT = saved
    check("state directories are created with their parents on first write", made)


def experiment_contracts():
    """The ablation arms change only the final-answer contract."""
    seen = {}

    def arm(contract, reply):
        s = agent.Session("p16", Profile("p16"), clock=dt.date(2020, 3, 1), contract=contract)
        sent = []

        class Once:
            async def send(self, prompt, schema=None):
                sent.append((prompt, schema))
                out = await agent.call_tool(s, "read_record", {})
                seen[contract] = out["content"][0]["text"]
                yield reply
                yield DONE
        s._client = Once()
        return anyio.run(agent.turn, s, "how am I doing?"), sent, s

    with tempfile.TemporaryDirectory() as temp:
        previous, audit.LOG_DIR = audit.LOG_DIR, Path(temp)
        try:
            free, free_sent, free_s = arm("free", {"type": "message", "phase": "final_answer",
                                                   "text": "You trained 3 times [00000000-e99]."})
            loose, loose_sent, _ = arm("cited_unchecked", final("Fine [00000000-e99]."))
        finally:
            audit.LOG_DIR = previous
    check("the free arm sends no schema, no context card, no citation policy and no cards",
          free_sent == [(free_sent[0][0], None)] and "evidence card" not in free_sent[0][0]
          and "evidence_cards" not in seen["free"]
          and evidence.POLICY not in agent.system_prompt(free_s)
          and free["reply"] == "You trained 3 times [00000000-e99]." and free["stop"] == "answered")
    check("the unchecked arm asks for citations but accepts an invented one without correction",
          loose_sent[0][1] == evidence.SCHEMA and "evidence_cards" in seen["cited_unchecked"]
          and loose["reply"] == "Fine [00000000-e99]." and not loose["execution"]["corrections"])
    check("the free arm keeps the workspace, so its plans are drafts like the product's",
          agent.FREE_ANSWER in agent.system_prompt(free_s)
          and workspace.INDEX in agent.system_prompt(free_s)
          and set(free["execution"]["final_answer"]) == {"answer", "outcome", "reason"}
          and "validate_training_plan" not in free_s.tools())


def reply_language_rule():
    check("the reply language follows the latest message, decided in code",
          agent.reply_language(chr(0x660E)) == "Chinese"
          and agent.reply_language("It has passed. What did I do last week?") == "English")


def http_guard():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"

    def status(path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(base + path, data, headers or {})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status
        except urllib.error.HTTPError as exc:
            return exc.code

    json_header = {"Content-Type": "application/json"}
    try:
        check("path-like participant ids are refused before touching files",
              status("/api/reset", {"pid": "../../etc/x"}, json_header) == 400
              and status("/api/audit?pid=../x") == 400
              and status("/api/snapshot") == 400)
        check("cross-site requests and form posts are refused",
              status("/api/reset", {"pid": "p16"},
                     {**json_header, "Origin": "http://evil.example"}) == 403
              and status("/api/tests", headers={"Sec-Fetch-Site": "cross-site"}) == 403
              and status("/api/reset", {"pid": "p16"},
                         {"Content-Type": "text/plain"}) == 415)
        check("a same-origin page request still works",
              status("/api/participants", headers={"Origin": base}) == 200)
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    audit_chain_and_archive()
    sensor_boundary_and_calories()
    tokens_are_per_turn()
    request_cap()
    failed_turn_commits_nothing()
    guideline_retrieval()
    one_review_per_signal()
    transcript_survives_the_page()
    readable_outputs()
    turn_time_budget()
    final_answer_corrections()
    experiment_contracts()
    borrowed_chatgpt_login()
    conversation_persists_per_participant()
    state_dirs_are_created_on_first_write()
    reply_language_rule()
    http_guard()
