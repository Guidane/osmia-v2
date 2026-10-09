"""
Run Osmia as a server:  python serve.py [--host 0.0.0.0] [--port 8000]

It serves Osmia with waitress (plus its static files) in a child process and
looks after it:

- when Osmia asks to restart (it exits with code 3, after a module change was
  queued on the Modules page), it starts it again, which applies the change;
- when Osmia stops right after starting, e.g. because a module is broken, it
  starts it once more in safe mode, without modules, so an administrator can
  log in and switch the module off;
- Ctrl+C stops it.
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from osmia.addons import RESTART_EXIT_CODE  # noqa: E402

QUICK_FAILURE_SECONDS = 60  # stopping sooner than this after a start counts as "did not start"


def say(text):
    print(text, flush=True)


def child(host, port, threads):
    """Serve Osmia in this process until it exits."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'osmia.settings')
    from django.contrib.staticfiles.handlers import StaticFilesHandler

    from osmia.wsgi import application  # applies a queued module change first

    app = StaticFilesHandler(application)  # CSS, JS and icons; images go through Osmia's own view
    try:
        from waitress import serve
    except ImportError:
        say('waitress is not installed (pip install -r requirements.txt); using the basic Python server.')
        from socketserver import ThreadingMixIn
        from wsgiref.simple_server import WSGIServer, make_server

        class ThreadingServer(ThreadingMixIn, WSGIServer):
            daemon_threads = True

        say(f'Osmia is running on http://{host}:{port}/')
        make_server(host, port, app, server_class=ThreadingServer).serve_forever()
        return
    say(f'Osmia is running on http://{host}:{port}/')
    # Big enough for a data export from another Osmia (Modules page); waitress buffers it to disk.
    serve(app, host=host, port=port, threads=threads, max_request_body_size=20 * 1024 ** 3)


def supervise(args):
    safe = False
    while True:
        env = {**os.environ, 'OSMIA_SERVE': '1', 'OSMIA_SAFE_MODE': '1' if safe else '', 'PYTHONUNBUFFERED': '1'}
        cmd = [sys.executable, str(Path(__file__).resolve()), '--child',
               '--host', args.host, '--port', str(args.port), '--threads', str(args.threads)]
        started = time.time()
        proc = subprocess.Popen(cmd, cwd=BASE_DIR, env=env)
        try:
            code = proc.wait()
        except KeyboardInterrupt:
            try:
                proc.wait(timeout=10)
            except (KeyboardInterrupt, subprocess.TimeoutExpired):
                proc.kill()
            say('Osmia stopped.')
            return 0
        if code == RESTART_EXIT_CODE:
            say('Restarting Osmia to apply the module changes...')
            safe = False
            continue
        if code == 0:
            return 0
        if time.time() - started < QUICK_FAILURE_SECONDS:
            if safe:
                say(f'Osmia did not start, even in safe mode (exit code {code}). See the messages above.')
                return code
            say(f'Osmia did not start (exit code {code}). Starting it in safe mode, without modules, '
                  'so a module can be switched off on the Modules page.')
            safe = True
            continue
        say(f'Osmia stopped unexpectedly (exit code {code}); starting it again.')
        safe = False
        time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description='Run Osmia.')
    parser.add_argument('--host', default=os.environ.get('OSMIA_HOST', '127.0.0.1'),
                        help='Address to listen on; 0.0.0.0 for every computer on the network. Default 127.0.0.1.')
    parser.add_argument('--port', type=int, default=int(os.environ.get('OSMIA_PORT', '8000')))
    parser.add_argument('--threads', type=int, default=8)
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        child(args.host, args.port, args.threads)
        return 0
    return supervise(args)


if __name__ == '__main__':
    sys.exit(main())
