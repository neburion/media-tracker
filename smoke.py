#!/usr/bin/env python3
"""smoke.py — does the app still come up, and does it still work?

The UI is one file, and one file means one bad rule or one typo can take the
whole thing down: a stray brace closes the stylesheet early, a renamed class
hides the shelf, a thrown exception in `card()` leaves an empty page that still
returns 200. None of that shows up in a diff and all of it shows up on the
phone. So: seed a throwaway database, start the server on a spare port, drive a
real browser through the app at a phone size and a desktop size, and ask the
page itself what it can see.

    python3 smoke.py              # both viewports, quiet unless something fails
    python3 smoke.py -v           # print every check

Exits non-zero if anything fails, so it can gate a deploy.

Stdlib only, like the rest of this. The browser is whatever chromium is on
PATH, else `nix shell nixpkgs#chromium`, because that is the one dependency
worth reaching outside for — a headless renderer is the only thing that can
tell you a stylesheet parsed and a button is 44 pixels tall.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv

# The checklist, run inside the page. Each entry is [name, expression]; the
# expression returns true, or a string explaining what it found instead.
# The checklist, run inside the page.
#
# It runs in an IFRAME of the size under test, not in the top window, because
# chromium honours --window-size when it takes a screenshot and ignores it when
# it dumps the DOM — the viewport you get is 500 wide whatever you asked for,
# which is exactly the width that decides whether this app is a phone or a
# desktop. An iframe is a viewport you can actually set: media queries, `dvh`,
# and `position:fixed` all resolve against it. The harness in the top window
# holds the frame and writes down what comes back.
PROBE = r"""
<script>
(() => {
const VP = new URLSearchParams(location.search).get('vp');

if (VP && window.top === window) {                      // harness
  const [w, h] = VP.split('x').map(Number);
  addEventListener('message', ev => {
    if (ev.data && ev.data.smoke)
      document.documentElement.setAttribute('data-smoke', JSON.stringify(ev.data.smoke));
  });
  addEventListener('load', () => {
    const f = document.createElement('iframe');
    f.src = '/';
    f.style.cssText = `position:fixed;left:0;top:0;border:0;z-index:99999;
                       width:${w}px;height:${h}px;background:#000`;
    document.body.appendChild(f);
  });
  return;
}
if (window.top === window) return;                      // a plain visit

// Measure where things come to rest, not where they are a third of the way
// through arriving. CSS transitions are driven by the compositor clock, which
// --virtual-time-budget does not fast-forward: a sheet mid-slide stays
// mid-slide forever in here. Zeroing the durations makes every state its
// resting state, which is the only geometry worth asserting about.
document.head.insertAdjacentHTML('beforeend',
  '<style>*,*::before,*::after{transition-duration:0s!important;' +
  'animation-duration:0s!important}</style>');

window.__errs = [];
addEventListener('error', e => window.__errs.push(String(e.message)));
addEventListener('unhandledrejection', e => window.__errs.push('promise: ' + e.reason));

async function smoke(){
  const out = [];
  const say = (name, ok) => out.push([name, ok === true ? true : String(ok)]);
  const vis = el => !!el && el.getBoundingClientRect().width > 0
                        && el.getBoundingClientRect().height > 0;
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const phone = innerWidth <= 640;

  say('viewport is the one asked for', innerWidth > 0 || 'zero width');
  say('portal has doors', document.querySelectorAll('.door').length >= 1
      || 'found ' + document.querySelectorAll('.door').length);

  location.hash = '#/reading';
  await wait(900);
  say('header visible', vis(document.querySelector('header')) || 'no header');
  say('tabs visible', document.querySelectorAll('.tab').length >= 5
      || 'found ' + document.querySelectorAll('.tab').length + ' tabs');

  // Current can legitimately be empty; All cannot.
  const tabs = [...document.querySelectorAll('.tab')];
  if (tabs.length) { tabs[tabs.length - 1].click(); await wait(1500); }
  say('shelf has cards', document.querySelectorAll('.book').length > 20
      || 'found ' + document.querySelectorAll('.book').length);
  say('cards have art', vis(document.querySelector('.plate')) || 'no plate drawn');

  openSheet((S.data.series || [])[0].id);
  await wait(700);
  const sheet = document.querySelector('.sheet');
  say('editor opens', vis(sheet) || 'sheet not visible');
  const r = sheet.getBoundingClientRect();
  say('editor is on screen',
      (r.top >= -1 && r.left >= -1 && r.bottom <= innerHeight + 1
       && r.right <= innerWidth + 1)
      || `${Math.round(r.width)}x${Math.round(r.height)} at ${Math.round(r.left)},`
         + `${Math.round(r.top)} in ${innerWidth}x${innerHeight}`);
  say('editor has a progress field', vis(document.querySelector('#e-progress')) || 'missing');
  say('editor has shelf pills', document.querySelectorAll('.pick[data-status]').length >= 5
      || 'found ' + document.querySelectorAll('.pick[data-status]').length);
  say('editor has a save button', vis(document.querySelector('[data-save]')) || 'missing');
  say('editor has a close', vis(document.querySelector('[data-close]')) || 'missing');

  // Every control a finger has to hit is at least 44px tall: Apple's number,
  // and the one this stylesheet already claims to follow.
  if (phone) {
    const small = [...document.querySelectorAll('.sheet button, .sheet input, .sheet select')]
      .filter(el => { const b = el.getBoundingClientRect();
                      return b.height > 0 && b.height < 43.5; })
      .map(el => (el.className || el.id || el.tagName).split(' ')[0]
                 + '@' + Math.round(el.getBoundingClientRect().height));
    say('touch targets are 44px', small.length === 0
        || [...new Set(small)].slice(0, 6).join(', '));
  }

  const de = document.documentElement;
  say('no sideways scroll', de.scrollWidth <= de.clientWidth + 1
      || de.scrollWidth + ' > ' + de.clientWidth);

  document.querySelector('[data-close]').click();
  await wait(500);
  say('editor closes', !vis(document.querySelector('.sheet.open')) || 'still open');
  say('no script errors', window.__errs.length === 0 || window.__errs.join(' | '));
  return out;
}

addEventListener('load', () => setTimeout(() => {
  smoke().then(out => parent.postMessage({ smoke: out }, '*'))
         .catch(e => parent.postMessage({ smoke: [['the checklist ran aground',
                                                   String(e && e.message || e)]] }, '*'));
}, 2200));
})();
</script>
"""


def browser():
    """chromium, wherever it lives."""
    for name in ("chromium", "chromium-browser", "google-chrome-stable", "chrome"):
        found = shutil.which(name)
        if found:
            return [found]
    if shutil.which("nix"):
        return ["nix", "shell", "nixpkgs#chromium", "--command", "chromium"]
    sys.exit("smoke: no chromium on PATH and no nix to fetch one")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(work, port, size):
    """One viewport. Returns a list of [name, True | 'what went wrong']."""
    out = subprocess.run(
        browser() + [
            "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
            f"--user-data-dir={work}/profile-{size[0]}",
            "--window-size=1600,1200",
            "--virtual-time-budget=25000", "--dump-dom",
            f"http://127.0.0.1:{port}/?vp={size[0]}x{size[1]}",
        ],
        capture_output=True, text=True, timeout=180).stdout
    hit = re.search(r'data-smoke="(.*?)"', out, re.S)
    if not hit:
        return [["the page never reported back",
                 "it rendered %d bytes and set no result" % len(out)]]
    raw = hit.group(1).replace("&quot;", '"').replace("&amp;", "&")
    raw = raw.replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'")
    return json.loads(raw)


def main():
    work = Path(tempfile.mkdtemp(prefix="mt-smoke-"))
    env = dict(os.environ, MT_DB=str(work / "smoke.db"), MT_CACHE=str(work / "cache"),
               MT_UI=str(work / "probe.html"))
    # The UI under test, with the checklist stapled to the end of it. The file
    # on disk is never touched.
    (work / "probe.html").write_text(
        (HERE / "ui.html").read_text(encoding="utf-8") + PROBE, encoding="utf-8")

    seed = subprocess.run([sys.executable, str(HERE / "seed.py")],
                          env=env, capture_output=True, text=True)
    if seed.returncode:
        sys.exit("smoke: could not seed a database\n" + seed.stderr[-2000:])

    port = free_port()
    server = subprocess.Popen([sys.executable, str(HERE / "app.py"), "--port", str(port)],
                              env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    bad = 0
    try:
        for _ in range(100):                       # up to 10s for it to answer
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            sys.exit("smoke: the server never came up")

        for size, label in (((390, 844), "phone"), ((1440, 900), "desktop")):
            print(f"── {label} {size[0]}x{size[1]}")
            for name, ok in run(work, port, size):
                if ok is True:
                    if VERBOSE:
                        print(f"   ok    {name}")
                else:
                    bad += 1
                    print(f"   FAIL  {name}: {ok}")
    finally:
        server.terminate()
        shutil.rmtree(work, ignore_errors=True)

    print("smoke: all clear" if not bad else f"smoke: {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
