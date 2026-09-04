# Palladium

A media server you run yourself, and invite your friends to.

Films and series from folders on your own machine, played in a browser, on a phone, or
on a television. No account anywhere, no subscription, nothing reported to anybody.

- **Server** — Python 3, standard library only, plus ffmpeg for playback that needs
  re-encoding.
- **Web client** — plain JavaScript, no framework, no build step.
- **Apps** — Android phone and Android TV, one Kotlin/Compose codebase.

## What it does

- Scans folders of films and series, identifies them against TMDB, and keeps the
  result in SQLite.
- Plays direct where the device can take the file, and re-encodes where it cannot —
  NVENC, AMF, Quick Sync or VideoToolbox, whichever the machine has.
- **Subtitles in time with the film.** Sync measures the film's own audio against the
  subtitle and corrects a constant offset, a drift, or a change part-way through. The
  correction is stored on the server, so everyone watching that file gets it.
- Invites: a link gives one person access to your library without an account.
- Watchlist, a shuffle for putting something on without choosing, and a watch log.

## Run from source

    python pd-server.py

Serves on <http://localhost:8765> and opens a browser. Options:

    --no-open          do not open a browser
    --port 9000        somewhere other than 8765
    --root PATH        keep settings, library and logs in PATH

`pd-tray.py` runs the same server behind a tray icon, which is how it starts at logon.

### What it needs

- Python 3.11 or newer.
- ffmpeg and ffprobe on PATH, or beside the program.
- A TMDB API key, entered in Settings, for artwork and titles.

## Check it still works

    python pd-smoke.py

Starts a server with an empty folder of its own on a free port, walks every page and
endpoint, and fails on any 500 or traceback. That empty folder is what a new install
looks like, which is the state hardest to test by using the program.

## Build the Windows installer

    py -3.13 pd-build-installer.py

Nuitka compiles the server and the tray icon; Inno Setup wraps them with the web
client. Needs a python.org Python (not the Microsoft Store one), Nuitka, Pillow,
pystray and Inno Setup 6 — the script says which are missing before it starts.

## Build the app

    cd android
    JAVA_HOME="C:/Program Files/Android/Android Studio/jbr" gradle assembleDebug
    cd .. && python pd-publish-apk.py

`pd-publish-apk.py` copies the APK into `static/` and writes the version the in-app
updater reads.

## Where things are kept

Run from source, everything sits beside the code. Installed, the program is in
`%LOCALAPPDATA%\Palladium` and everything it writes is in `%APPDATA%\Palladium`:

| file | what |
|---|---|
| `settings.json` | everyone's settings, marks, positions, subtitle corrections |
| `library.db` | the library index |
| `config.json` | server addresses and tokens |
| `invites.json` | live keys to the library |
| `library.json` | the TMDB key and which folders to scan |

None of those are in the repository, and none of them should be.

## Licence

GNU Affero General Public License v3.0 - the full text is in [LICENSE](LICENSE).

In short: use it, change it, share it. If you give a changed version to anybody else,
or run one as a service other people use, that version's source has to be available to
them under the same terms.

Copyright (C) 2026 Grovestick Studios
