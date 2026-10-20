#!/usr/bin/env python3
"""Smoke tests for the Flyby landing page.

Static checks read index.html directly. Browser checks open the real page in
headless Chrome at desktop and phone sizes, drive it with tests/driver.js over
the DevTools protocol (--remote-debugging-pipe, no extra dependencies), and
report what passed.

Usage: python3 tests/smoke.py [path/to/index.html]
Set CHROME=/path/to/chrome if Chrome isn't in a standard location.
"""
import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRIVER = Path(__file__).resolve().parent / 'driver.js'

REQUIRED_IDS = ['navbar', 'hamburger', 'mobileNav', 'heroCanvas', 'carouselContainer',
                'carouselTrack', 'carouselUrl', 'cPrev', 'cNext']

VIEWPORTS = [  # (label, width, height, mobile)
    ('desktop', 1440, 900, False),
    ('mobile', 390, 844, True),
]

# Installed before any page script runs, so errors thrown during load count too.
ERROR_HOOK = ('window.__smokeErrors = [];'
              'addEventListener("error", e => __smokeErrors.push(e.message + " (" + (e.filename || "").split("/").pop() + ":" + e.lineno + ")"));'
              'addEventListener("unhandledrejection", e => __smokeErrors.push("Unhandled rejection: " + e.reason));')

TTY = sys.stdout.isatty()
GREEN, RED, DIM, BOLD, RESET = ('\033[32m', '\033[31m', '\033[2m', '\033[1m', '\033[0m') if TTY else ('',) * 5


# ── Static checks ────────────────────────────────────────────

class Tags(HTMLParser):
    """Collects every start tag as (tag, attrs)."""
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def static_checks(page):
    src = page.read_text(encoding='utf-8')
    parser = Tags()
    parser.feed(src)
    tags = parser.tags
    has_class = lambda attrs, cls: cls in (attrs.get('class') or '').split()
    results = []
    check = lambda name, ok, detail='': results.append({'name': name, 'ok': bool(ok), 'detail': detail})

    dash_lines = [str(i) for i, line in enumerate(src.splitlines(), 1) if '—' in line]
    check('no em dashes', not dash_lines, 'found on line(s) ' + ', '.join(dash_lines))

    ids = [a['id'] for _, a in tags if a.get('id')]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    check('element ids are unique', not dupes, ', '.join(dupes))
    missing = [i for i in REQUIRED_IDS if i not in ids]
    check('elements the script relies on exist', not missing, 'missing #' + ', #'.join(missing))

    anchors = sorted({a['href'][1:] for _, a in tags if (a.get('href') or '').startswith('#') and len(a['href']) > 1})
    broken = [a for a in anchors if a not in ids]
    check('in-page links point at real sections', not broken, 'no target for #' + ', #'.join(broken))

    local = sorted({a.get(k) for _, a in tags for k in ('src', 'href')
                    if a.get(k) and not re.match(r'^(https?:|//|#|mailto:|tel:|data:)', a.get(k))})
    absent = [p for p in local if not (page.parent / p).exists()]
    check('local assets exist', not absent, ', '.join(absent))

    no_alt = sum(1 for t, a in tags if t == 'img' and 'alt' not in a)
    check('images have alt text', no_alt == 0, f'{no_alt} <img> without alt')

    slides = [a for _, a in tags if has_class(a, 'c-slide')]
    dots = [a for _, a in tags if has_class(a, 'c-dot')]
    check('carousel has one dot per slide', slides and len(slides) == len(dots), f'{len(slides)} slides, {len(dots)} dots')
    check('carousel dots are indexed 0..n-1', [d.get('data-idx') for d in dots] == [str(i) for i in range(len(dots))],
          ' '.join(str(d.get('data-idx')) for d in dots))
    check('every slide has a data-url', all(s.get('data-url') for s in slides))

    counts = [a.get('data-count') for _, a in tags if has_class(a, 'stat-num')]
    check('stat counters have numeric targets', counts and all((c or '').isdigit() for c in counts), ', '.join(map(str, counts)))

    tally = [a for _, a in tags if a.get('data-tally-open')]
    check('waitlist buttons are wired to Tally', tally, 'no data-tally-open elements')
    check('Tally embed script is included', 'tally.so/widgets/embed.js' in src)
    return results


# ── Browser checks ───────────────────────────────────────────

def find_chrome():
    candidates = [os.environ.get('CHROME'),
                  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                  '/Applications/Chromium.app/Contents/MacOS/Chromium']
    candidates += [shutil.which(n) for n in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser')]
    return next((c for c in candidates if c and os.path.exists(c)), None)


class Chrome:
    """Minimal DevTools-protocol client over --remote-debugging-pipe.

    Chrome reads commands from fd 3 and writes replies to fd 4, each message
    being JSON terminated by a NUL byte.
    """

    def __init__(self, binary, profile):
        cmd_read, self._write = os.pipe()
        self._read, reply_write = os.pipe()

        def wire_fds():  # runs in the child between fork and exec, before other fds are closed
            a, b = os.dup(cmd_read), os.dup(reply_write)
            os.dup2(a, 3)
            os.dup2(b, 4)

        # pass_fds keeps only fds 3 and 4 open in Chrome. Letting it inherit anything else
        # (make's pipes, the terminal's output pipe) means Chrome's crash-reporter helper,
        # which outlives the browser, holds them open and the command never seems to finish.
        self.proc = subprocess.Popen(
            [binary, '--headless=new', '--remote-debugging-pipe', '--disable-gpu', '--no-first-run',
             '--no-default-browser-check', '--disable-background-timer-throttling',
             '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows',
             '--disable-breakpad', f'--user-data-dir={profile}', 'about:blank'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=wire_fds, pass_fds=(3, 4))
        os.close(cmd_read)
        os.close(reply_write)
        self._id = 0
        self._buf = b''
        self._events = []

    def _next_message(self, deadline):
        while b'\0' not in self._buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Chrome stopped responding')
            ready, _, _ = select.select([self._read], [], [], remaining)
            if ready:
                chunk = os.read(self._read, 1 << 16)
                if not chunk:
                    raise RuntimeError('Chrome exited unexpectedly')
                self._buf += chunk
        raw, self._buf = self._buf.split(b'\0', 1)
        return json.loads(raw)

    def send(self, method, params=None, session=None, timeout=30):
        self._id += 1
        msg = {'id': self._id, 'method': method, 'params': params or {}}
        if session:
            msg['sessionId'] = session
        os.write(self._write, json.dumps(msg).encode() + b'\0')
        deadline = time.monotonic() + timeout
        while True:
            reply = self._next_message(deadline)
            if reply.get('id') == self._id:
                if 'error' in reply:
                    raise RuntimeError(f'{method}: {reply["error"].get("message")}')
                return reply.get('result', {})
            self._events.append(reply)

    def wait_for(self, event, session, timeout=30):
        deadline = time.monotonic() + timeout
        while True:
            for i, msg in enumerate(self._events):
                if msg.get('method') == event and msg.get('sessionId') == session:
                    return self._events.pop(i)
            self._events.append(self._next_message(deadline))

    def close(self):
        try:
            os.write(self._write, json.dumps({'id': 0, 'method': 'Browser.close'}).encode() + b'\0')
            self.proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
            self.proc.wait()
        finally:
            os.close(self._write)
            os.close(self._read)


def browser_checks(binary, page, profile, width, height, mobile):
    chrome = Chrome(binary, profile)
    try:
        target = chrome.send('Target.createTarget', {'url': 'about:blank'})['targetId']
        sid = chrome.send('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
        chrome.send('Emulation.setDeviceMetricsOverride',
                    {'width': width, 'height': height, 'deviceScaleFactor': 1, 'mobile': mobile}, sid)
        # Tally only matters when a waitlist button is clicked; keep runs offline-safe.
        chrome.send('Network.enable', session=sid)
        chrome.send('Network.setBlockedURLs', {'urls': ['*tally.so*']}, sid)
        chrome.send('Page.enable', session=sid)
        chrome.send('Page.addScriptToEvaluateOnNewDocument', {'source': ERROR_HOOK}, sid)
        chrome.send('Page.navigate', {'url': page.as_uri()}, sid)
        chrome.wait_for('Page.loadEventFired', sid)
        reply = chrome.send('Runtime.evaluate', {'expression': DRIVER.read_text(encoding='utf-8'),
                                                 'awaitPromise': True, 'returnByValue': True}, sid, timeout=90)
        if 'exceptionDetails' in reply:
            detail = reply['exceptionDetails'].get('exception', {}).get('description') or reply['exceptionDetails'].get('text')
            return None, [{'name': 'test driver ran', 'ok': False, 'detail': detail}]
        data = reply['result']['value']
        return data['viewport'], data['results']
    except (RuntimeError, TimeoutError, OSError) as err:
        return None, [{'name': 'browser session completed', 'ok': False, 'detail': str(err)}]
    finally:
        chrome.close()


# ── Report ───────────────────────────────────────────────────

def print_group(title, results):
    print(f'\n{BOLD}{title}{RESET}')
    for r in results:
        mark = f'{GREEN}✓{RESET}' if r['ok'] else f'{RED}✗{RESET}'
        detail = f'  {DIM}{r["detail"]}{RESET}' if r['detail'] and not r['ok'] else ''
        print(f'  {mark} {r["name"]}{detail}', flush=True)


def main():
    page = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / 'index.html'
    groups = [('Static checks · ' + page.name, static_checks(page))]
    print_group(*groups[-1])

    binary = find_chrome()
    if not binary:
        groups.append(('Browser checks', [{'name': 'Chrome is available', 'ok': False,
                                            'detail': 'not found; install Chrome or set CHROME=/path/to/chrome'}]))
        print_group(*groups[-1])
    else:
        with tempfile.TemporaryDirectory(prefix='flyby-smoke-') as tmp:
            for label, width, height, mobile in VIEWPORTS:
                viewport, results = browser_checks(binary, page, Path(tmp) / label, width, height, mobile)
                groups.append((f'Browser checks · {label}' + (f' {viewport}' if viewport else ''), results))
                print_group(*groups[-1])

    all_results = [r for _, results in groups for r in results]
    failed = sum(not r['ok'] for r in all_results)
    colour = RED if failed else GREEN
    print(f'\n{colour}{BOLD}{len(all_results) - failed} passed, {failed} failed{RESET}')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
