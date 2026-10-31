# LiveCap

Live captions and translation in the browser, with optional read-aloud, from a menu bar app on an Apple Silicon Mac. Speech recognition is [Parakeet v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) on the Mac's GPU. Translation and speech also run locally, so no audio or text leaves the Mac.

- **Input:** a microphone in the browser, or a network stream from OBS over RTMP or SRT.
- **Spoken languages:** English, German, French, Spanish, Italian, Dutch, Polish.
- **Caption languages:** those seven plus Portuguese (Brazil), Russian, Ukrainian, Czech, Swedish, Danish, Finnish, Greek, Romanian, Hungarian, Turkish, Arabic, Hindi, Vietnamese, Chinese, Japanese and Korean. Each viewer picks their own.
- **Read-aloud:** press Play to hear the captions in the selected language, paced to match the speaker. Chinese is captions only.

## Pages

| Address | For | What it does |
| --- | --- | --- |
| `/` | The public | Read the captions, choose a language, press Play to listen, change text size. Nothing else. |
| `/admin` | You | All of the above, plus the audio source, microphone, spoken language, status and theme. Needs the admin password. |

## Theme and logo

On the admin page, choose **Edit theme** to set the public page's heading, a logo and your colors. Colors preview on the admin page as you choose them. **Save** applies everything, including on viewers' pages that are already open, and **Cancel** discards it.

- **Colors:** the main color is used for buttons and the live light, and the other two are the header and footer, and the caption background. Each accepts a code from a brand guide, such as `#1a2b3c`, and any you leave on Default follow the viewer's device in light or dark.
- **Readability:** text colors are chosen automatically to reach at least 4.5:1 contrast, the WCAG AA level, on whichever colors you pick. If a button's color would disappear into the color behind it, the button uses the text color there instead.
- **Logo:** PNG, JPEG, WebP, GIF or SVG, up to 5 MB, shown 44 pixels tall. Use a transparent background, and check that the logo itself shows up against your header color, because its colors aren't adjusted.
- **Where it's kept:** in `~/Library/Application Support/LiveCap/data/branding`. It survives restarts and app updates. Back up that folder to keep it when moving to another Mac.

## Install

LiveCap needs an Apple Silicon Mac with macOS 14 or later.

1. Download `LiveCap.dmg` from the [latest release](https://github.com/big-barlowe/LiveCap/releases/latest), open it, and drag LiveCap to Applications.
2. Open LiveCap from Applications. The free build isn't notarized, so macOS blocks it the first time. Go to System Settings, then Privacy & Security, and choose Open Anyway.
3. Wait for the speech model to download. This happens once and takes 2.4 GB. The voice is already in the app.
4. Use the LiveCap icon in the menu bar. It opens the pages, copies the public address, shows the admin password, and quits.

LiveCap keeps the Mac awake while it runs and restarts the server if it stops. Quitting LiveCap stops everything. On a Mac Mini used as a server, also do the following:

- Choose Open at Login in the LiveCap menu.
- Turn on automatic login in System Settings, so LiveCap starts after a reboot.
- Allow incoming connections if macOS asks.
- Run `sudo pmset -a autorestart 1` so the Mac restarts after a power cut.

Settings and models are in `~/Library/Application Support/LiveCap`, and logs are in `~/Library/Logs/LiveCap`. To remove LiveCap, drag it to the Trash and delete those two folders.

To build the disk image yourself, run `./macos/build.sh`. It needs the Xcode Command Line Tools and writes `dist/LiveCap.dmg`. Set `SIGN_ID` and `NOTARY_PROFILE` to sign and notarize it with an Apple Developer account, as described at the top of the script.

## Updates

Choose **Check for Updates…** in the LiveCap menu. If a newer version is available, LiveCap shows what changed and downloads it while captions keep running. It then asks before restarting, which stops captions for about a minute. Choose Later to install it from the same menu when nothing is live. Your settings, theme and models are kept.

LiveCap only contacts GitHub when you choose this. An update is installed only if it carries a valid signature from the publisher's key, so a download that was altered, or published by anyone else, is deleted and nothing changes. The account running LiveCap needs permission to change the Applications folder, otherwise install the new version by hand from the releases page.

## Access

The public page is at `http://<mac-ip>:8000`. On the Mac itself, the admin page is at <http://localhost:8000/admin>. The browser asks for a username (anything works) and the password.

If you don't set a password, one is generated on first start. To choose your own, set `LIVECAP_ADMIN_PASSWORD` in the settings file.

From another machine, open the admin page at `https://<mac-ip>:8443/admin`. HTTPS keeps the password off the network in plain text, and browsers only allow the microphone on HTTPS or localhost. The app creates a self-signed certificate, so accept the browser's warning once. To use your own certificate, put `cert.pem` and `key.pem` in `~/Library/Application Support/LiveCap/data/certs` and restart LiveCap. The public can use plain HTTP on port 8000.

The first time a caption language is chosen, its translation model (about 90 MB, or two if neither language is English) is downloaded and kept in the data folder. Captions in that language start once the download finishes, and a banner shows this meanwhile. If the download fails, viewers see the original text and it retries after 30 seconds. Set `LIVECAP_PRELOAD` to fetch languages at startup, which is worth doing before a live event. Transcription and the voice need no network once installed.

## Speech recognition

| Measure | Parakeet v3 |
| --- | --- |
| Word errors: audiobook | 4.2% |
| Word errors: real meetings | 12.6% |
| Word errors: earnings calls | 17.1% |
| Word errors: other European languages | 3.3% to 16.2% |
| Delay, median and worst | About 1 s and 4 to 6 s |
| Memory for speech alone | About 1.5 GB |

Word errors come from small samples and include differences in how numbers are written. The delay was measured on an M2, with continuous synthetic speech. An M1 will be a little slower, and LiveCap re-reads less often if a read takes too long.

## How captions are timed

Speech is re-read every second, with the surrounding words in view. A sentence appears once two reads agree on it and more words have followed. If the speaker pauses, it appears about a second later.

While a sentence builds, viewers reading the spoken language see the words two reads agree on, in grey. Viewers of a translation see three dots, then the finished sentence, because translating half a sentence would keep changing it. A sentence that ends on an unfinished word, such as "we had hoped to stay, but", waits up to 1.5 seconds for the speaker to carry on, then the two parts are joined.

## Voice

The voice is Supertonic, which is built into the app and reads every caption language except Chinese. Choose it with `LIVECAP_VOICE`: `F1` to `F5` or `M1` to `M5`.

It follows the speaker's overall pace, between 0.85× and 1.25×, with a minimum pace so it doesn't drag in slower languages. If reading falls behind, new sentences are read faster, up to 1.4×.

## Performance

- **Memory:** with four caption languages and read-aloud on an M2, LiveCap used about 2.9 GB of memory and kept up with live speech.
- **Limit the languages** with `LIVECAP_LANGUAGES`. Otherwise any visitor can make the Mac download and load any of the 24 languages. Combine it with `LIVECAP_PRELOAD`.
- **Start without signing in:** set `LIVECAP_SOURCE=obs` to listen for OBS as soon as LiveCap starts. The microphone needs a browser on the admin page, so a Mac running unattended should normally use OBS.

## Connect OBS

1. On the admin page, choose **OBS** as the audio source, or set `LIVECAP_SOURCE=obs`. The choice resets when LiveCap restarts unless that setting is used.
2. In OBS, open **Settings → Stream** and set **Service** to **Custom**. Use either:

| Protocol | Server | Stream key |
| --- | --- | --- |
| RTMP | `rtmp://<mac-ip>:1935/live` | `stream` |
| SRT | `srt://<mac-ip>:9000` | leave empty |

3. Click **Start Streaming**.

Use SRT if the network between OBS and the Mac is unreliable. OBS can only send to one main destination unless you use a multi-stream plugin, so running this alongside a broadcast needs a plugin or a second OBS output.

Only the first audio track is used, so check that it carries the speech. Under **Output → Advanced**, the track is set in **Streaming → Audio Track**.

While OBS is the selected source, the public page says "Waiting for the stream to start" until audio arrives, then "Live". It goes back to waiting about 3 seconds after the stream stops. A stream with silent audio counts as live, so it says "Live" even when nobody is speaking.

## Settings

Choose Open Settings File in the LiveCap menu, make your changes, then quit and reopen LiveCap.

| Variable | Default | Meaning |
| --- | --- | --- |
| `LIVECAP_ADMIN_PASSWORD` | generated | Password for `/admin` |
| `LIVECAP_INPUT_LANG` | `en` | Spoken language at startup: `en de fr es it nl pl`, or `auto` |
| `LIVECAP_SOURCE` | off | Set to `obs` to listen for OBS from startup |
| `LIVECAP_LANGUAGES` | all | Comma-separated caption languages viewers may choose, such as `es,fr` |
| `LIVECAP_VOICE` | `M1` | Voice: `F1` to `F5`, `M1` to `M5` |
| `LIVECAP_PRELOAD` | empty | Comma-separated caption languages to fetch at startup, such as `es,fr` |

## Good to know

- **Screens stay on while reading.** On HTTPS, including behind a reverse proxy, the page uses the browser's wake lock. On plain HTTP, browsers don't offer that, so phones play a hidden silent video instead. Either one may need a tap on the page, and a short prompt shows until then. The video counts as playing sound, so it can pause music from another app. Screens stay on only while the page is showing, and this uses more battery than letting the screen sleep.
- **Choose the spoken language when you know it.** Auto-detect uses the recognized words, so a very short sentence can be mislabelled and translated from the wrong language.
- **Don't expose the OBS ports publicly.** The admin page is protected, but anyone who can reach ports 1935 and 9000 while OBS is the selected source can send audio to it. Only publish port 8000 or 8443 to the public, and firewall the input ports.
- **All viewers share one transcription stream.** Starting a microphone in a second browser takes over from the first.
- **False captions are filtered, not impossible.** Speech models can invent phrases like "Thank you" from quiet sounds, so a voice detector (Silero) keeps non-speech away from them. Singing, and speech from a TV or the next room, will still be captioned. Mute or duck music in the OBS track you send.
- **Machine translation is literal and sentence by sentence.** It works best for clear speech and is not suitable where accuracy is critical. Japanese was the weakest language in testing.
- **Keep read-aloud away from the microphone.** In a test with a real microphone and speakers on one MacBook, word errors went from 5.9% to 47% with read-aloud playing, because the voice and the speaker get mixed together. Use headphones, or listen on another device out of the microphone's range. Sound from OBS isn't affected unless your OBS audio includes that voice. The admin page warns you when both are on in the same browser tab.
- **Read-aloud runs behind the captions.** Only finished sentences are read, so it follows the caption delay. If more than 20 seconds of reading is waiting, it skips older sentences to stay live.

## Publishing a new version

Commit and push your changes, then run:

```sh
./macos/release.sh 1.0.1 "What changed"
```

This builds the app, checks that it starts, signs the disk image, commits and tags the version, and creates the GitHub release that installed copies find. Pushing code alone doesn't update anyone, because only a release does. It needs the [GitHub CLI](https://cli.github.com) signed in with `gh auth login`.

Each version number must be higher than the last. An installed copy has no way to roll back, so to undo a bad release, publish a fixed one with a higher number.

**Keep the signing key safe.** It's at `~/.livecap/update-signing-key`, outside the repository, and must never be committed. Save a copy somewhere private, such as a password manager. To publish from another Mac, put the same key at that path. If the key is lost, installed copies refuse every new version, and each one has to be reinstalled by hand from a build with a new key. Anyone who gets the key, and also gets access to your GitHub account, could publish an update that installed copies would accept.

## Layout

```
app/vad.py         voice detector that keeps non-speech away from recognition
app/recognizers.py speech recognition
app/transcriber.py re-reads buffered speech and releases settled sentences
app/sentences.py   sentence rules and rejoining of unfinished sentences
app/engine.py      recognition on its own thread, source switching
app/hub.py         per-language translation and fan-out to viewers
app/translate.py   Argos Translate models on CTranslate2
app/tts.py         the voice and its pacing
app/obs.py         ffmpeg listeners for RTMP and SRT
app/branding.py    the saved logo and theme, and the colors worked out from them
app/main.py        web server and WebSocket endpoints
app/static/        the public page, admin page and shared captions code
macos/             the menu bar app, updater, disk image build and release script
```

Tests, which need the Python dependencies installed: `pip install -r requirements-mac.txt pytest && python -m pytest tests`.

## Licences

Parakeet v3 is under CC BY 4.0, and the app lists its bundled components under About LiveCap. LiveCap's dependencies are open source. The Supertonic model is under OpenRAIL-M, which limits certain uses, and its project is archived. The voice and translation models have their own licences, so check them on [supertonic-3](https://huggingface.co/Supertone/supertonic-3) and [Argos Translate](https://github.com/argosopentech/argos-translate) before commercial use.
