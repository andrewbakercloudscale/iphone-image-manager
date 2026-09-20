# Operational scripts

Copies of the job scripts that run from `~/.iphone-image/` on the Mac. That
directory is not in git, so this folder is the record of what actually ran.
Paths inside are hardcoded to that machine on purpose: these are not a product,
they are the log of a job. Copy them back with `cp scripts/*.sh ~/.iphone-image/`
after a fresh clone.

| script | what it does |
|---|---|
| `cycle.sh` | fetch -> upload -> release, repeating; empties the Bin itself when every item in it is a released asset |
| `after-video.sh` | waits for the video job, starts screenshots only if it finished cleanly |
| `watch-video.sh` | every 10 min records that the job is ALIVE; the last line says when it died |

Never edit one of these in place while a job runs from it: bash reads scripts
incrementally. Write `x.sh.new` and `mv` it over.
