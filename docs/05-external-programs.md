# External programs (the `exec` action)

The most powerful action in the menu engine. You can wire any shell
command to any menu hotkey, with the user's terminal bridged to the
process's stdin/stdout, with optional dropfile generation. Use it for:

- Running door games not yet supported as a first-class type
- A weather forecast (curl + wttr.in)
- An AI chat front-end
- A guestbook script
- A markov-chain text generator
- Anything you can shell out

## Configuring an exec item

In **Admin → BBS Menus → edit a menu → Add new item**:

- `action_type` = `exec`
- `action_args` = either a plain command line OR a JSON object

### Plain string form

```
curl -s wttr.in/?A0Tn0
```

That's it. Output streams to the user's terminal until the command
exits.

### JSON form

```json
{
  "cmd": "/opt/doors/lord/lord.sh",
  "name": "Legend of the Red Dragon",
  "dropfile": "door32.sys",
  "cwd": "/opt/doors/lord"
}
```

| key       | purpose                                                    |
| --------- | ---------------------------------------------------------- |
| `cmd`     | shell command (required)                                   |
| `name`    | label printed when launching/exiting (optional)            |
| `dropfile`| `door.sys`, `dorinfo`, `door32.sys` — generates one before launch |
| `cwd`     | working directory                                          |

## Variable substitution

In `cmd` you can use:

- `{user}`     — the BBS username
- `{userid}`   — numeric user id
- `{dropdir}`  — the directory where the dropfile was written

So `lord.sh -drop {dropdir}` works.

Do **not** wrap these tokens in your own quotes (e.g. `'{user}'`) —
the engine already shell-quotes every substituted value before
splicing it into the command, so `{user}`/`{userid}`/`{dropdir}` are
always safe to use bare, even when a username contains a space or
apostrophe. Adding your own quotes around an already-quoted value
breaks the shell's own parsing (a loud syntax error, not a silent
failure) rather than working.

## Safety

`exec` runs whatever the sysop configured, **as the user that runs the
`anetbbs` service** (telnet/SSH/rlogin all run inside this one unified
service). There is no sandbox. Treat this as a sysop-trust feature
only. Never let regular users edit menu items.

## Sample weather door

`/admin/bbs-menus/` has a "**Add sample items**" button that adds:

- Hotkey **2** → exec `curl -s wttr.in/?A0Tn0` (named "Weather")
- Hotkey **1** → ansi `welcome` (replay the welcome banner)

Use these to confirm `exec` and `ansi` work on your install before
rolling out custom doors.
