import os
import time

from app import create_app
from app.agent.cycle import run_evaluation_cycle

CYCLE_SECONDS = 30


def main():
    print("agent_worker started", flush=True)
    app = None
    while True:
        connection = os.environ.get("SOMEE_SQLSERVER_CONNECTION_STRING", "").strip()
        if not connection:
            print("event store is not configured; idle cycle", flush=True)
        else:
            if app is None:
                app = create_app()
            evaluated = run_evaluation_cycle(app)
            print("evaluation cycle finished; recommendations={0}".format(evaluated), flush=True)
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    main()
