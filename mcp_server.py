import sys

from app import create_app
from app.mcp.protocol import handle_message
from app.mcp.stdio import read_message, write_message


def main():
    app = create_app()
    while True:
        message = read_message(sys.stdin.buffer)
        if message is None:
            return
        response = handle_message(message, app)
        if response is not None:
            write_message(sys.stdout.buffer, response)


if __name__ == "__main__":
    main()
