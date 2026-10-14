"""Open the local Aegis dashboard when starting via RUN.bat."""
import threading
import webbrowser

import uvicorn


def launch():
    webbrowser.open('http://127.0.0.1:8765', new=2)


if __name__ == '__main__':
    threading.Timer(2.0, launch).start()
    uvicorn.run('server:app', host='127.0.0.1', port=8765, log_level='warning', access_log=False)
