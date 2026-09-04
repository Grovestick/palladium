# What is here that somebody else wrote

Two players are carried in this repository as built files, because the web client has
no build step and a browser has to be handed something it can read. Both are permissive
licences, compatible with the AGPL, and both require their copyright notices to travel
with the code - which is what this file and `static/licenses/` are for.

| File | Project | Licence |
| --- | --- | --- |
| `static/dash.all.min.js` | [dash.js](https://github.com/Dash-Industry-Forum/dash.js), Dash Industry Forum | BSD 3-Clause - [text](static/licenses/dash.js-LICENSE.txt) |
| `static/hls.min.js` | [hls.js](https://github.com/video-dev/hls.js), Dailymotion | Apache 2.0 - [text](static/licenses/hls.js-LICENSE.txt) |

Everything else in this repository is Palladium's own, © 2026 Grovestick Studios,
under the AGPL-3.0 in [LICENSE](LICENSE).

The Android app's dependencies - Media3, Compose, the Cast SDK - are fetched by Gradle
at build time and are not carried here; their licences are their own.
