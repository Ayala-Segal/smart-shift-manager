import os
import time

CYCLE_SECONDS = 30


def run_cycle():
    connection = os.environ.get("SOMEE_SQLSERVER_CONNECTION_STRING", "")
    if not connection:
        print("event store is not configured; idle cycle", flush=True)
        return
    print(
        "event store is configured; evaluation is not implemented in this skeleton",
        flush=True,
    )


def main():
    print("agent_worker started", flush=True)
    while True:
        run_cycle()
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    main()
