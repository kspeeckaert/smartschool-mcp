# smartschool-mcp

Version 1.0.20261018

smartschool-mcp is a read-only MCP server for one Smartschool student account. MCP (Model Context Protocol) is the protocol that lets an AI app such as Claude call tools on your computer. The server gives three tools: the unread messages, the open tasks, and the lessons for one day.

The server uses the unofficial Python library [smartschool](https://github.com/svaningelgem/smartschool) ([PyPI](https://pypi.org/project/smartschool/)) as a dependency. The server does not copy the library code. When the library gets a new release, the server can use it without a change to this repository. Smartschool has no official API for students and parents. If Smartschool changes its internal endpoints, the tools can stop working until the library gets an update.

The server is for personal use. It knows only the task types of one school: GR, KL, HU and ME. It reads the Smartschool planner ("Mijn planner"), not the classic agenda.

## Tools

| Tool | Parameters | Result |
| --- | --- | --- |
| `get_unread_messages` | none | The unread inbox messages: id, sender, subject, date, and attachment flag. |
| `get_tasks` | `days` (1 to 35, default 35), `types` (list of GR, KL, HU, ME, default all) | The open tasks after the current time: day, start, type, type name, course, and title. |
| `get_lessons` | `day` (YYYY-MM-DD, default tomorrow) | One entry for each time slot: start, end, course, topics, and the open tasks for that course in that slot. |

| Type | Name in the web app |
| --- | --- |
| GR | Grote toets |
| KL | Kleine toets |
| HU | Huistaak |
| ME | Meebrengen |

All tools are read-only. `get_unread_messages` returns the message headers only. When a client opens a message, Smartschool marks the message as read. For this reason, the tool does not get the message body.

`get_tasks` does not show the tasks that you marked as done. It also does not show the tasks that start at or before the current time. The sort order is day, then GR, KL, HU, ME, then other types, then time. A to-do without an assignment type has its planner type (`planned-to-dos`) as the type. If you give `types`, the to-dos without a type are not in the result.

`get_lessons` shows two lessons of the same course in the same slot as one entry, the same as the web app ("2 lessen"). On weekends and holidays, the list of lessons is empty.

All dates and times use the Europe/Brussels time zone.

## Requirements

When the MCP client starts the server, [uv](https://docs.astral.sh/uv/) downloads the server and its dependencies. Install uv first. You do not need to install Python packages yourself.

## Add the server to Claude Desktop

1. Open `~/Library/Application Support/Claude/claude_desktop_config.json`.
2. Add the `smartschool` entry below to `mcpServers`.
3. Replace the four values in `env` with your login data.
4. Restart Claude Desktop.

```json
{
  "mcpServers": {
    "smartschool": {
      "command": "uvx",
      "args": [
        "--upgrade-package", "smartschool",
        "--from", "git+https://github.com/kspeeckaert/smartschool-mcp",
        "smartschool-mcp"
      ],
      "env": {
        "SMARTSCHOOL_USERNAME": "your.username",
        "SMARTSCHOOL_PASSWORD": "your password",
        "SMARTSCHOOL_MAIN_URL": "myschool.smartschool.be",
        "SMARTSCHOOL_MFA": "YYYY-mm-dd"
      }
    }
  }
}
```

| Variable | Value |
| --- | --- |
| `SMARTSCHOOL_USERNAME` | Your Smartschool username. |
| `SMARTSCHOOL_PASSWORD` | Your Smartschool password. |
| `SMARTSCHOOL_MAIN_URL` | The host name of your school, without `https://`. |
| `SMARTSCHOOL_MFA` | Your birthday in the format `YYYY-mm-dd`. If your account uses two-factor authentication, the secret of your authenticator app. |

The configuration file contains your password in plain text. Do not commit it to version control and do not share it.

If Claude Desktop cannot find `uvx`, use the full path for `command`. To find the full path, run `which uvx` in Terminal.

The library keeps the login cookies in `~/.cache/smartschool`. When the cookies expire, the server logs in again.

## Updates

On each start, uvx gets the latest commit of this repository from GitHub. A change that you push is active after the next restart of the MCP client.

The option `--upgrade-package smartschool` tells uvx to get the latest release of the smartschool library from PyPI on each start. Without this option, uvx can use an older version that it keeps in its cache.

## Use a fixed version

To use a fixed version of the smartschool library, add `--with` and the version to `args`. The upgrade option then has no effect on the library, because the fixed version has priority.

```json
"args": [
  "--upgrade-package", "smartschool",
  "--with", "smartschool[mfa]==0.12.1",
  "--from", "git+https://github.com/kspeeckaert/smartschool-mcp",
  "smartschool-mcp"
]
```

To use a fixed version of this server, add a tag or a commit to the GitHub URL, for example `git+https://github.com/kspeeckaert/smartschool-mcp@v1.0.20261018`. You must create the tag in the repository first.

## Show debug output

To show debug output, add `-v` after `smartschool-mcp` in `args`. The server writes the debug lines to stderr. stdout carries the MCP messages, so the server never writes log lines there. Claude Desktop keeps the stderr output in `~/Library/Logs/Claude/mcp-server-smartschool.log`.

## Test the server locally

To start the server from a local copy, run this command in the project folder. The command waits for MCP messages on stdin.

```zsh
uv run smartschool-mcp
```

To test the tools in the MCP Inspector, run this command. The Inspector needs Node.js (`npx`).

```zsh
uv run --with "mcp[cli]" mcp dev src/smartschool_mcp/server.py
```

## Change the version

The version has the format `[major].[minor].[iso date]`, for example `1.0.20261018`. The version is in `src/smartschool_mcp/__init__.py`. `pyproject.toml` reads it from there. Also change the version line at the top of this README and in the docstring of `server.py`.
