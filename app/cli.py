import click
from sqlalchemy.exc import OperationalError

from app.commands.define_shift import CommandRejected, define_shift
from app.projections import project_shift_board
from app.queries.board import get_shift_board


def register_cli(app):
    @app.cli.command("init-accounts")
    def init_accounts():
        """Create the users table. Accounts are stored outside the event log."""
        from app.auth.service import ensure_accounts_table

        ensure_accounts_table()
        click.echo("accounts table is ready")

    def require_somee():
        if not app.config["SOMEE_SQLSERVER_CONNECTION_STRING"].strip():
            raise click.ClickException(
                "Set SOMEE_SQLSERVER_CONNECTION_STRING to the connection string copied from Somee."
            )

    @app.cli.command("define-shift")
    @click.option("--date", required=True, help="Shift date, YYYY-MM-DD.")
    @click.option("--start", "start_time", required=True, help="Start time, HH:MM.")
    @click.option("--end", "end_time", required=True, help="End time, HH:MM.")
    @click.option("--role", "required_role", required=True, help="Required role.")
    @click.option("--headcount", required=True, type=int, help="Required headcount.")
    @click.option("--note", default="", help="Optional note.")
    def define_shift_command(date, start_time, end_time, required_role, headcount, note):
        """Append a ShiftDefined event. This does not update the board."""
        require_somee()
        try:
            event = define_shift(
                actor_id="manager-1",
                actor_role="manager",
                shift_date=date,
                start_time=start_time,
                end_time=end_time,
                required_role=required_role,
                required_headcount=headcount,
                note=note,
            )
        except CommandRejected as error:
            raise click.ClickException(str(error)) from error
        click.echo(
            "stored event_id={0} type={1} aggregate_id={2} version={3}".format(
                event.event_id,
                event.event_type,
                event.aggregate_id,
                event.version,
            )
        )
        click.echo("The board is unchanged until you run: flask project-board")

    @app.cli.command("project-board")
    def project_board_command():
        """Build read_shift_board from new events."""
        require_somee()
        processed = project_shift_board()
        click.echo("projected {0} events".format(processed))

    @app.cli.command("index-policies")
    def index_policies():
        """Embed labor-agreement clauses in the vector store."""
        from app.retrieval.service import ensure_agreement_indexed
        from app.retrieval.store import RetrievalUnavailable

        try:
            stored = ensure_agreement_indexed(app.config["VECTOR_STORE_PATH"], force=True)
        except RetrievalUnavailable as error:
            raise click.ClickException(str(error)) from error
        click.echo("indexed {0} policy paragraphs".format(stored))

    @app.cli.command("show-board")
    def show_board_command():
        """Print the shift board from the read model."""
        require_somee()
        try:
            rows = get_shift_board()
        except OperationalError as error:
            raise click.ClickException(
                "The board table is missing. Run: flask project-board"
            ) from error
        if not rows:
            click.echo("No shifts in the selected range")
            return
        for row in rows:
            click.echo(
                "{0}  {1}  {2}  {3}-{4}  {5}  {6}/{7}  {8}  waiting {9}".format(
                    row.shift_id,
                    row.shift_date.isoformat(),
                    row.weekday,
                    row.start_time,
                    row.end_time,
                    row.required_role,
                    row.assigned_count,
                    row.required_headcount,
                    row.status,
                    row.waiting_candidates,
                )
            )
