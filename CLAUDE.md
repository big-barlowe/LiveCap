# LiveCap: notes for working on this project

Read `README.md` first for what the app does and how it's laid out. This file holds what the code doesn't show: why things are the way they are, and the mistakes already made.

This repository is public. Don't add names of organizations, addresses, network details or anything else private to this file, commits or test data.

## Priorities

- **Audience:** hearing-impaired and foreign-language viewers, who depend on the captions. It replaces a paid service, so it has to hold up during a live event.
- **Accuracy and low delay both matter.** Don't trade one away without saying so.
- **Captions shouldn't rewrite themselves.** Viewers find changing text hard to read, which is why partial words only go to people reading the spoken language, and only once two reads agree.
- **Read-aloud should be one steady voice** with no jumps, cut-offs or long pauses, and shouldn't lag far behind.
- **Text must stay readable on any theme color.** The server picks text colors in `app/branding.py`, so don't add a setting that can break contrast.

## Scope

macOS on Apple Silicon only, as a menu bar app distributed from a DMG. The main target is an M1 Mac Mini. Docker, Linux and Raspberry Pi support were dropped on purpose, so don't add them back or suggest them unprompted. The Whistle and Piper code left in `app/` isn't used by the Mac app, and Piper crashes natively on a Mac.

## Running things

Use a throwaway Python 3.13 environment for tests:

```sh
pip install -r requirements-mac.txt pytest
python -m pytest tests
```

To run the server from source without touching the installed app's data, or its ports if it's running, set `LIVECAP_DATA` to a temporary folder and use other ports with `LIVECAP_HTTP_PORT`, `LIVECAP_HTTPS_PORT`, `LIVECAP_RTMP_PORT` and `LIVECAP_SRT_PORT`. Set `LIVECAP_RECOGNIZER=parakeet`, `LIVECAP_ADMIN_PASSWORD`, `LIVECAP_PARAKEET_DIR`, `LIVECAP_SUPERTONIC_DIR` and `LIVECAP_FFMPEG` too, then run `python -m app.main`. An installed app's `Contents/Resources` holds the voice model and `ffmpeg`, and its data folder holds the speech model.

`./macos/build.sh` builds `dist/LiveCap.dmg`. The script is zsh, not bash. Clear `HF_HUB_OFFLINE` first, or a stale test setting can break the build.

## Testing traps

These all produced wrong conclusions or missed bugs before:

- **Read-aloud through speakers near a live mic garbles captions.** Word errors went from 5.9% to 47% in a real test. Test with OBS audio, headphones or a separate device, or the results mislead.
- **Playwright turns off the browser cache when given `http_credentials`.** This hid a bug where a new page ran yesterday's cached code. Send the login as an `Authorization` header in `extra_http_headers` instead.
- **Playwright's `evaluate()` counts as a user tap.** Anything testing behavior without a tap has to run on the page's own timer.
- **`127.0.0.1` and `localhost` are secure contexts.** To test plain HTTP, use another name with Chrome's `--host-resolver-rules=MAP captions.test 127.0.0.1`.
- **Phones differ by connection.** Over plain HTTP there's no Wake Lock, so the page uses a hidden video. Over HTTPS, including through a reverse proxy, it uses Wake Lock, which iOS appears to refuse until a tap. Test both. Nothing here proves behavior on a real iPhone, so say so.
- **WebKit's rules for keeping the screen on are strict:** the video needs picture and sound, can't be muted, and can't use the `loop` attribute. See `app/static/keep-awake.js`.
- **The bundled `ffmpeg` is audio-only and can only receive,** so sending a test stream needs a full `ffmpeg`.
- **Don't quit or disturb an installed LiveCap that may be serving a live event.** Test on other ports, with a separate data folder.
- **The menu and dialogs can't be clicked by automated tools here.** Compiling with `-D UPDATE_TEST` answers the update dialogs automatically and allows a test feed through `LIVECAP_UPDATE_FEED`. Those hooks must never reach a shipping build. A test copy needs its own bundle ID, because macOS won't start a second copy of an app whose ID is already running.

## Releasing

1. Commit and push the changes, then run `./macos/release.sh <version> "What changed"`.
2. Pushing code alone updates nobody. Installed copies only see GitHub releases.
3. Installed copies can't roll back, so undo a bad release by publishing a fix with a higher version number.
4. Only publish or push when the user asks.

The signing key is at `~/.livecap/update-signing-key`, outside the repository. **Never read it into output, commit it or copy it into the project.** Its public half is `LiveCapUpdateKey` in `macos/Info.plist`, and replacing that pair stops installed copies from accepting updates.

Commits must use the account's GitHub no-reply address, set per clone with `git config`, because commits in a public repository publish the address they carry. Check it before the first commit on a new machine.

## Working style

- Reproduce a bug before fixing it, and test the fix against the same reproduction.
- Rerun tests on the built app, not only from source, before calling something done.
- Say plainly what wasn't tested. Real OBS, a real iPhone and a real M1 are the usual gaps.
- The user isn't relying on code review, so explain changes by what people will see, and give exact steps for anything they have to do.
