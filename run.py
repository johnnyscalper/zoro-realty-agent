"""Run locally; optionally open the browser after this server has started."""
import argparse
import threading
import time
import webbrowser
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11 이상이 필요합니다.")
    from app.config import ConfigurationError, Settings
    try:
        cfg = Settings.from_env()
    except ConfigurationError as exc:
        raise SystemExit(str(exc)) from None
    import uvicorn
    from app.main import create_app
    browser_host = "127.0.0.1" if cfg.app_host == "0.0.0.0" else cfg.app_host
    url = f"http://{browser_host}:{cfg.app_port}"
    print(f"집의 다음: http://{cfg.app_host}:{cfg.app_port}")
    print("종료: Ctrl+C. 키를 수정한 뒤에는 서버를 다시 시작하세요.")
    server = uvicorn.Server(uvicorn.Config(create_app(cfg), host=cfg.app_host, port=cfg.app_port,
                                          access_log=False, log_level="warning"))
    if args.open_browser:
        threading.Thread(target=open_when_ready, args=(server, url), daemon=True).start()
    server.run()


def open_when_ready(server, url, *, wait=time.sleep, opener=webbrowser.open):
    # This exact server instance must be listening. An old app on the same port
    # must not be mistaken for successful startup of the new folder.
    for _ in range(150):
        if server.should_exit:
            return
        if server.started:
            try:
                opener(url)
            except Exception:
                pass  # The printed local URL remains available.
            return
        wait(0.2)


if __name__ == "__main__":
    main()
