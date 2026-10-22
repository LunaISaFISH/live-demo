"""What the model actually receives through Codex, captured offline.

By default Codex adds context of its own: the working directory, the real
date and timezone, sandbox text, the machine's installed skills,
multi-agent instructions and extra tools. agent.CODEX_OVERRIDES removes
them, mostly through keys that Codex's config schema lists but its prose
docs do not. This starts the real Codex app-server with FitCoach's
configuration, points its model provider at a local capture server and
checks the request it sends. No network, no login, no model call.

Run: ../.venv/bin/python test_codex_request.py
"""
import datetime as dt
import http.server
import json
import os
import re
import threading
from pathlib import Path

import subprocess
import tempfile

import anyio
import codex_cli_bin

import agent
import evidence
import skills
from profile import Profile


def check(name, condition):
    assert condition, name
    print("PASS", name)


def capture_request() -> dict:
    captured = []

    class Capture(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            captured.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
            self.send_response(400)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"capture only","type":"invalid_request_error"}}')

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Capture)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    provider = (f'model_providers.capture={{name="capture", '
                f'base_url="http://127.0.0.1:{httpd.server_port}/v1", wire_api="responses", '
                f'env_key="FITCOACH_CAPTURE_KEY", request_max_retries=0, stream_max_retries=0}}')
    saved = agent.CODEX_OVERRIDES, os.environ.get("OPENAI_API_KEY")
    s = agent.Session("p16", Profile("p16"), clock=dt.date(2020, 3, 1))
    agent.CODEX_OVERRIDES = (*agent.CODEX_OVERRIDES, provider)
    os.environ.update(OPENAI_API_KEY="capture-only", FITCOACH_CAPTURE_KEY="capture-only")
    conversation = agent.CodexConversation(agent.MODEL, agent.system_prompt(s), agent.tool_specs(s),
                                           home=Path(tempfile.mkdtemp()) / "codex", with_skills=True,
                                           with_workspace=True)
    conversation.provider = "capture"
    try:
        conversation.start()

        async def one_turn():
            with anyio.fail_after(60):
                return [ev async for ev in conversation.send("USER_MESSAGE_MARKER", evidence.SCHEMA)]
        events = anyio.run(one_turn)
    finally:
        conversation.close()
        httpd.shutdown()
        agent.CODEX_OVERRIDES = saved[0]
        os.environ.pop("FITCOACH_CAPTURE_KEY", None)
        if saved[1] is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = saved[1]
    check("the turn ends with a terminal event even when the provider refuses",
          events and events[-1]["type"] == "completed" and events[-1]["status"] != "completed")
    requests = [r for r in captured if r.get("input")]
    check("Codex sends exactly one model request for the turn", len(requests) == 1)
    return requests[0], agent.system_prompt(s), agent.tool_specs(s)


def texts(item) -> list[str]:
    content = item.get("content")
    if isinstance(content, str):
        return [content]
    return [c.get("text", "") for c in content or [] if isinstance(c, dict)]


def main():
    request, instructions, specs = capture_request()
    items = request["input"]
    messages = [(i.get("role"), t) for i in items if i.get("type") != "additional_tools"
                for t in texts(i) if t]
    check("the only messages are the coach instructions, Codex's skill list and the user's message",
          [m[0] for m in messages] == ["developer", "developer", "user"]
          and messages[0][1] == instructions and messages[2][1] == "USER_MESSAGE_MARKER")
    listed = re.findall(r"^- ([\w-]+): .*\(file: (.+)\)$", messages[1][1], re.M)
    check("the skill list holds exactly the coach's skills, from this conversation's own copy",
          [name for name, _ in listed] == [s["name"] for s in skills.catalogue()]
          and all("/work/.agents/skills/" in path and str(Path.home()) not in path for _, path in listed))
    check("the request carries no top-level instructions or tools of its own",
          not request.get("instructions") and not request.get("tools"))
    check("the final answer's shape is enforced by the model API, not asked for in prose",
          request.get("text", {}).get("format", {}).get("schema", {}).get("required")
          == evidence.SCHEMA["required"])
    toolsets = [i for i in items if i.get("type") == "additional_tools"]
    exec_tools = [t for group in toolsets for ns in group["tools"] for t in ns.get("tools", [])]
    check("the only tool the model sees is exec, the gpt-5.6 tool host",
          [t["name"] for t in exec_tools] in (["exec"], ["exec", "wait"]))
    declared = re.findall(r"### `(\w+)`", exec_tools[0]["description"])
    # exec_command and write_stdin are Codex's shell; apply_patch follows the
    # model catalogue with no switch, and the sandbox lets it write only the workspace.
    check("exec declares the coach tools, Codex's shell and apply_patch, nothing else",
          sorted(declared) == sorted([*(spec["name"] for spec in specs),
                                      "exec_command", "write_stdin", "apply_patch"]))
    whole = json.dumps(request, ensure_ascii=False)
    today = dt.date.today()
    check("the real date is absent: only the simulation clock tells the model what day it is",
          str(today) not in whole and f"{today:%B} {today.day}" not in whole
          and "current_date" not in whole and "timezone" not in whole)
    check("no environment, permissions, bundled or personal skills, multi-agent text or home path is added",
          not any(marker in whole for marker in (
              "environment_context", "permissions instructions", "imagegen", "openai-docs",
              "multi_agent_mode", "You are `/root`", str(Path.home()))))



def sandbox_confines_the_shell():
    """Codex's own sandbox, with the conversation's permission profile, run directly.

    apply_patch goes through the same profile; the live e2e run exercises it.
    """
    binary = codex_cli_bin.bundled_codex_path()   # wherever this Python installed it
    root = Path(tempfile.mkdtemp())
    home, user_home, folder = root / "codex", root / "home", root / "work" / ".agents" / "skills"
    space = root / "work" / "workspace"
    user_home.mkdir(parents=True)
    home.mkdir()
    (space / "plans" / "versions").mkdir(parents=True)
    skills.install(folder)
    overrides = [o for o in (*agent.CODEX_OVERRIDES, *agent.sandbox_overrides(str(folder), str(space)))
                 if o.startswith(("default_permissions", "permissions.", "shell_environment_policy"))]
    env = {**os.environ, "CODEX_HOME": str(home), "HOME": str(user_home), "FITCOACH_SECRET_PROBE": "x"}

    def run(*command):
        return subprocess.run([str(binary), "sandbox", *(x for o in overrides for x in ("-c", o)),
                               "--", *command], capture_output=True, text=True, env=env,
                              cwd=root, timeout=60)
    read = run("cat", str(folder / "planning" / "SKILL.md"))
    check("the shell can read the skills and their references",
          read.returncode == 0 and read.stdout.startswith("---\nname: planning")
          and run("cat", str(folder / "references" / "asking.md")).returncode == 0)
    here = Path(agent.__file__).resolve().parent
    private_files = (
        agent.DENIED_READS[0] / "pmdata" / "p05" / "pmsys" / "wellness.csv",   # raw records
        here / "agent.py", here.parent / "README.md", Path.home() / ".codex" / "auth.json")
    assert all(p.is_file() for p in private_files[:3]), "sandbox probes need real source files"
    blocked = [run("head", "-c", "1", str(p)) for p in private_files]
    check("it cannot read raw records, code, the project or login files",
          all(r.returncode != 0 and not r.stdout for r in blocked))
    wrote = run("sh", "-c", f"echo '# Memory' > {space / 'memory.md'} && mkdir -p {space / 'plans'} "
                            f"&& echo '{{}}' > {space / 'plans' / 'draft.json'}")
    check("it can write its workspace: memory and the plan draft",
          wrote.returncode == 0 and (space / "memory.md").read_text() == "# Memory\n"
          and (space / "plans" / "draft.json").exists())
    for target in (space / "plans" / "versions" / "001.json", folder / "written.md",
                   root / "work" / "written.md", here / "written.md"):
        run("sh", "-c", f"echo x > {target}")
    check("it cannot write saved plans, the skills, the rest of its folder or the project",
          not any(p.exists() for p in (space / "plans" / "versions" / "001.json", folder / "written.md",
                                        root / "work" / "written.md", here / "written.md")))
    check("the network probe is installed and executable inside the sandbox",
          run("curl", "--version").returncode == 0)
    # A local listener distinguishes blocked networking from an offline internet connection.
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        check("it has no network", run("curl", "-s", "-m", "3",
              f"http://127.0.0.1:{httpd.server_port}/").returncode != 0)
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
    sandbox_confines_the_shell()
