"""Initial schema — all tables

Revision ID: 0001
Revises:
Create Date: 2026-05-06

对应文档: docs/08_database_schema.md
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 扩展
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # updated_at 触发器函数
    op.execute("""
        CREATE OR REPLACE FUNCTION trigger_set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
        $$ LANGUAGE plpgsql
    """)

    # strategy_instances
    op.create_table(
        "strategy_instances",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False, unique=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("instance_name", sa.String(100), nullable=False, unique=True),
        sa.Column("strategy_type", sa.String(50), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("config_yaml", sa.Text, nullable=False),
        sa.Column("config_version", sa.Integer, server_default="1"),
        sa.Column("allocated_capital", sa.Numeric(20, 8), nullable=False),
        sa.Column("available_capital", sa.Numeric(20, 8), nullable=False),
        sa.Column("total_pnl", sa.Numeric(20, 8), server_default="0"),
        sa.Column("total_fees_paid", sa.Numeric(20, 8), server_default="0"),
        sa.Column("total_funding_received", sa.Numeric(20, 8), server_default="0"),
        sa.Column("total_positions_opened", sa.Integer, server_default="0"),
        sa.Column("total_positions_closed", sa.Integer, server_default="0"),
        sa.Column("last_active_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("halt_reason", sa.String(255)),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )
    op.create_index("idx_strategy_instances_enabled", "strategy_instances", ["enabled"])
    op.create_index("idx_strategy_instances_type", "strategy_instances", ["strategy_type"])
    op.execute("""CREATE TRIGGER set_updated_at_strategy_instances
        BEFORE UPDATE ON strategy_instances FOR EACH ROW
        EXECUTE FUNCTION trigger_set_updated_at()""")

    # opportunities
    op.create_table(
        "opportunities",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("strategy_instance", sa.String(100), nullable=False),
        sa.Column("strategy_type", sa.String(50), nullable=False),
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("instrument_type", sa.String(20), nullable=False),
        sa.Column("apr_pct", sa.Numeric(10, 4)),
        sa.Column("funding_rate", sa.Numeric(10, 8)),
        sa.Column("basis_pct", sa.Numeric(10, 4)),
        sa.Column("premium_pct", sa.Numeric(10, 4)),
        sa.Column("iv_rv_ratio", sa.Numeric(10, 4)),
        sa.Column("cex_dex_spread_pct", sa.Numeric(10, 4)),
        sa.Column("spot_orderbook_depth_usd", sa.Numeric(20, 2)),
        sa.Column("perp_orderbook_depth_usd", sa.Numeric(20, 2)),
        sa.Column("status", sa.String(20), server_default="detected"),
        sa.Column("rejection_reason", sa.String(255)),
        sa.Column("detected_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("used_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )
    op.create_index("idx_opportunities_strategy", "opportunities", ["strategy_instance"])
    op.create_index("idx_opportunities_symbol", "opportunities", ["exchange", "symbol"])
    op.create_index("idx_opportunities_status", "opportunities", ["status"])

    # positions
    op.create_table(
        "positions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("strategy_instance", sa.String(100), nullable=False),
        sa.Column("strategy_type", sa.String(50), nullable=False),
        sa.Column("opportunity_id", sa.BigInteger, sa.ForeignKey("opportunities.id")),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("notional_usd", sa.Numeric(20, 2), nullable=False),
        sa.Column("margin_used", sa.Numeric(20, 8), nullable=False),
        sa.Column("target_delta", sa.Numeric(10, 6), server_default="0"),
        sa.Column("current_delta", sa.Numeric(10, 6)),
        sa.Column("delta_drift_pct", sa.Numeric(10, 4)),
        sa.Column("target_apr_pct", sa.Numeric(10, 4)),
        sa.Column("realized_pnl", sa.Numeric(20, 8), server_default="0"),
        sa.Column("unrealized_pnl", sa.Numeric(20, 8), server_default="0"),
        sa.Column("funding_received", sa.Numeric(20, 8), server_default="0"),
        sa.Column("fees_paid", sa.Numeric(20, 8), server_default="0"),
        sa.Column("slippage_loss", sa.Numeric(20, 8), server_default="0"),
        sa.Column("opened_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("closed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("holding_hours", sa.Numeric(10, 2)),
        sa.Column("exit_reason", sa.String(50)),
        sa.Column("exit_pnl_pct", sa.Numeric(10, 4)),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )
    op.create_index("idx_positions_strategy", "positions", ["strategy_instance", "status"])
    op.create_index("idx_positions_status", "positions", ["status"])
    op.execute("""CREATE TRIGGER set_updated_at_positions
        BEFORE UPDATE ON positions FOR EACH ROW
        EXECUTE FUNCTION trigger_set_updated_at()""")

    # position_status_log
    op.create_table(
        "position_status_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("position_id", sa.BigInteger, sa.ForeignKey("positions.id"), nullable=False),
        sa.Column("old_status", sa.String(20)),
        sa.Column("new_status", sa.String(20), nullable=False),
        sa.Column("changed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION log_position_status_change()
        RETURNS TRIGGER AS $$
        BEGIN
            IF OLD.status IS DISTINCT FROM NEW.status THEN
                INSERT INTO position_status_log (position_id, old_status, new_status)
                VALUES (NEW.id, OLD.status, NEW.status);
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER log_position_status
        AFTER UPDATE OF status ON positions FOR EACH ROW
        EXECUTE FUNCTION log_position_status_change()""")

    # position_legs
    op.create_table(
        "position_legs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("position_id", sa.BigInteger,
                  sa.ForeignKey("positions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("instrument_type", sa.String(20), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("size", sa.Numeric(20, 8), nullable=False),
        sa.Column("entry_price", sa.Numeric(20, 8), nullable=False),
        sa.Column("current_price", sa.Numeric(20, 8)),
        sa.Column("leverage", sa.Numeric(10, 2), server_default="1"),
        sa.Column("margin", sa.Numeric(20, 8), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(20, 8), server_default="0"),
        sa.Column("unrealized_pnl", sa.Numeric(20, 8), server_default="0"),
        sa.Column("funding_paid", sa.Numeric(20, 8), server_default="0"),
        sa.Column("fees_paid", sa.Numeric(20, 8), server_default="0"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("opened_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("closed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )
    op.create_index("idx_position_legs_position", "position_legs", ["position_id"])
    op.create_index("idx_position_legs_exchange_symbol", "position_legs", ["exchange", "symbol"])
    op.create_index("idx_position_legs_status", "position_legs", ["status"])
    op.execute("""CREATE TRIGGER set_updated_at_position_legs
        BEFORE UPDATE ON position_legs FOR EACH ROW
        EXECUTE FUNCTION trigger_set_updated_at()""")

    # orders
    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("leg_id", sa.BigInteger, sa.ForeignKey("position_legs.id")),
        sa.Column("position_id", sa.BigInteger, sa.ForeignKey("positions.id")),
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("exchange_order_id", sa.String(100)),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("order_type", sa.String(20), nullable=False),
        sa.Column("time_in_force", sa.String(10)),
        sa.Column("post_only", sa.Boolean, server_default="false"),
        sa.Column("reduce_only", sa.Boolean, server_default="false"),
        sa.Column("requested_size", sa.Numeric(20, 8), nullable=False),
        sa.Column("filled_size", sa.Numeric(20, 8), server_default="0"),
        sa.Column("requested_price", sa.Numeric(20, 8)),
        sa.Column("avg_fill_price", sa.Numeric(20, 8)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(50)),
        sa.Column("error_message", sa.Text),
        sa.Column("submitted_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("filled_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("canceled_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )
    op.create_index("idx_orders_leg", "orders", ["leg_id"])
    op.create_index("idx_orders_position", "orders", ["position_id"])
    op.create_index("idx_orders_exchange_status", "orders", ["exchange", "status"])
    op.execute("""CREATE TRIGGER set_updated_at_orders
        BEFORE UPDATE ON orders FOR EACH ROW
        EXECUTE FUNCTION trigger_set_updated_at()""")

    # risk_events
    op.create_table(
        "risk_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("tier", sa.Integer, nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("strategy_instance", sa.String(100)),
        sa.Column("position_id", sa.BigInteger, sa.ForeignKey("positions.id")),
        sa.Column("metric_name", sa.String(50)),
        sa.Column("metric_value", sa.Numeric(20, 8)),
        sa.Column("threshold", sa.Numeric(20, 8)),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("action_taken", sa.Text),
        sa.Column("acknowledged", sa.Boolean, server_default="false"),
        sa.Column("acknowledged_by", sa.String(100)),
        sa.Column("acknowledged_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )
    op.create_index("idx_risk_events_severity", "risk_events", ["severity"])
    op.create_index("idx_risk_events_strategy", "risk_events", ["strategy_instance"])
    op.create_index("idx_risk_events_type", "risk_events", ["event_type"])

    # notifications
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("level", sa.String(20), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("strategy_instance", sa.String(100)),
        sa.Column("position_id", sa.BigInteger, sa.ForeignKey("positions.id")),
        sa.Column("risk_event_id", sa.BigInteger, sa.ForeignKey("risk_events.id")),
        sa.Column("channels", postgresql.ARRAY(sa.Text), nullable=False),
        sa.Column("delivered_channels", postgresql.ARRAY(sa.Text)),
        sa.Column("delivery_attempts", sa.Integer, server_default="0"),
        sa.Column("read", sa.Boolean, server_default="false"),
        sa.Column("read_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("actioned", sa.Boolean, server_default="false"),
        sa.Column("metadata", postgresql.JSONB),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )
    op.create_index("idx_notifications_level", "notifications", ["level"])
    op.create_index("idx_notifications_read", "notifications", ["read"])
    op.create_index("idx_notifications_strategy", "notifications", ["strategy_instance"])

    # system_state
    op.create_table(
        "system_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="running"),
        sa.Column("halted_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("halt_reason", sa.Text),
        sa.Column("last_heartbeat", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("NOW()")),
        sa.Column("version", sa.String(20)),
        sa.Column("total_capital", sa.Numeric(20, 8)),
        sa.Column("total_pnl_today", sa.Numeric(20, 8), server_default="0"),
        sa.Column("total_pnl_all_time", sa.Numeric(20, 8), server_default="0"),
        sa.Column("daily_drawdown_pct", sa.Numeric(10, 4), server_default="0"),
        sa.Column("weekly_drawdown_pct", sa.Numeric(10, 4), server_default="0"),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.CheckConstraint("id = 1", name="system_state_single_row"),
    )
    op.execute("INSERT INTO system_state (id, state, version) VALUES (1, 'running', '0.1.0')")

    # user_settings
    op.create_table(
        "user_settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("notification_matrix", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("scan_min_apr", sa.Numeric(10, 4), server_default="10.0"),
        sa.Column("default_position_size_usd", sa.Numeric(20, 2), server_default="500"),
        sa.Column("max_concurrent_positions", sa.Integer, server_default="5"),
        sa.Column("telegram_chat_id", sa.String(50)),
        sa.Column("discord_webhook_url_encrypted", sa.Text),
        sa.Column("email_to", sa.String(100)),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.CheckConstraint("id = 1", name="user_settings_single_row"),
    )
    # Insert the single settings row; use json_build_object to avoid colon-parsing
    # issues in SQLAlchemy text() (":false" would be misread as a bind param).
    op.execute("""
        INSERT INTO user_settings (id, notification_matrix) VALUES (
            1,
            json_build_object(
                'info',     json_build_object('telegram', false, 'discord', false, 'email', false, 'toast', true,  'system', false),
                'success',  json_build_object('telegram', true,  'discord', false, 'email', false, 'toast', true,  'system', false),
                'warn',     json_build_object('telegram', true,  'discord', true,  'email', false, 'toast', true,  'system', true),
                'critical', json_build_object('telegram', true,  'discord', true,  'email', true,  'toast', true,  'system', true)
            )
        )
    """)

    # TimescaleDB hypertables
    for table_def in [
        ("funding_rate_history", "7 days", "exchange,symbol", [
            sa.Column("time", sa.TIMESTAMP(timezone=True), nullable=False),
            sa.Column("exchange", sa.String(30), nullable=False),
            sa.Column("symbol", sa.String(30), nullable=False),
            sa.Column("instrument_type", sa.String(20), nullable=False),
            sa.Column("funding_rate", sa.Numeric(15, 10), nullable=False),
            sa.Column("apr_pct", sa.Numeric(10, 4)),
            sa.Column("next_funding_time", sa.TIMESTAMP(timezone=True)),
            sa.Column("funding_interval_hours", sa.Integer),
            sa.Column("mark_price", sa.Numeric(20, 8)),
            sa.Column("index_price", sa.Numeric(20, 8)),
        ]),
        ("price_history", "1 day", "exchange,symbol,interval", [
            sa.Column("time", sa.TIMESTAMP(timezone=True), nullable=False),
            sa.Column("exchange", sa.String(30), nullable=False),
            sa.Column("symbol", sa.String(30), nullable=False),
            sa.Column("instrument_type", sa.String(20), nullable=False),
            sa.Column("interval", sa.String(10), nullable=False),
            sa.Column("open", sa.Numeric(20, 8), nullable=False),
            sa.Column("high", sa.Numeric(20, 8), nullable=False),
            sa.Column("low", sa.Numeric(20, 8), nullable=False),
            sa.Column("close", sa.Numeric(20, 8), nullable=False),
            sa.Column("volume", sa.Numeric(20, 8)),
            sa.Column("quote_volume", sa.Numeric(20, 8)),
            sa.Column("trades_count", sa.Integer),
        ]),
        ("pnl_timeseries", "1 day", "strategy_instance", [
            sa.Column("time", sa.TIMESTAMP(timezone=True), nullable=False),
            sa.Column("strategy_instance", sa.String(100), nullable=False),
            sa.Column("position_id", sa.BigInteger),
            sa.Column("price_pnl", sa.Numeric(20, 8), server_default="0"),
            sa.Column("funding_pnl", sa.Numeric(20, 8), server_default="0"),
            sa.Column("basis_pnl", sa.Numeric(20, 8), server_default="0"),
            sa.Column("theta_pnl", sa.Numeric(20, 8), server_default="0"),
            sa.Column("vega_pnl", sa.Numeric(20, 8), server_default="0"),
            sa.Column("fees_paid", sa.Numeric(20, 8), server_default="0"),
            sa.Column("slippage_loss", sa.Numeric(20, 8), server_default="0"),
            sa.Column("net_pnl", sa.Numeric(20, 8), nullable=False),
            sa.Column("cumulative_pnl", sa.Numeric(20, 8)),
            sa.Column("total_capital", sa.Numeric(20, 8)),
            sa.Column("available_capital", sa.Numeric(20, 8)),
        ]),
        ("orderbook_snapshots", "1 day", None, [
            sa.Column("time", sa.TIMESTAMP(timezone=True), nullable=False),
            sa.Column("exchange", sa.String(30), nullable=False),
            sa.Column("symbol", sa.String(30), nullable=False),
            sa.Column("instrument_type", sa.String(20), nullable=False),
            sa.Column("bids", postgresql.JSONB, nullable=False),
            sa.Column("asks", postgresql.JSONB, nullable=False),
            sa.Column("spread", sa.Numeric(15, 8)),
            sa.Column("mid_price", sa.Numeric(20, 8)),
            sa.Column("depth_5_bid_usd", sa.Numeric(20, 2)),
            sa.Column("depth_5_ask_usd", sa.Numeric(20, 2)),
        ]),
    ]:
        tname, chunk_interval, compress_by, cols = table_def
        op.create_table(tname, *cols)
        op.execute(f"""
            SELECT create_hypertable('{tname}', 'time',
                chunk_time_interval => INTERVAL '{chunk_interval}', if_not_exists => TRUE)
        """)
        if compress_by:
            op.execute(f"""
                ALTER TABLE {tname} SET (
                    timescaledb.compress,
                    timescaledb.compress_segmentby = '{compress_by}'
                )
            """)
            op.execute(f"""
                SELECT add_compression_policy('{tname}', INTERVAL '7 days', if_not_exists => TRUE)
            """)

    retention_policies = {
        "funding_rate_history": "730 days",
        "price_history": "730 days",
        "pnl_timeseries": "1825 days",
        "orderbook_snapshots": "30 days",
    }
    for tname, retention in retention_policies.items():
        op.execute(f"""
            SELECT add_retention_policy('{tname}', INTERVAL '{retention}', if_not_exists => TRUE)
        """)

    # 视图
    op.execute("""
        CREATE OR REPLACE VIEW v_current_positions AS
        SELECT p.id, p.uuid, p.strategy_instance, p.strategy_type,
               p.notional_usd, p.margin_used,
               p.realized_pnl + p.unrealized_pnl AS total_pnl,
               p.target_apr_pct, p.opened_at,
               EXTRACT(EPOCH FROM (NOW() - p.opened_at)) / 3600 AS hours_open,
               array_agg(jsonb_build_object(
                   'exchange', l.exchange, 'symbol', l.symbol,
                   'side', l.side, 'size', l.size, 'entry_price', l.entry_price
               )) AS legs
        FROM positions p
        LEFT JOIN position_legs l ON l.position_id = p.id
        WHERE p.status = 'open'
        GROUP BY p.id
    """)
    op.execute("""
        CREATE OR REPLACE VIEW v_unacknowledged_risk_events AS
        SELECT * FROM risk_events
        WHERE acknowledged = FALSE AND severity IN ('warn', 'critical')
        ORDER BY severity DESC, created_at DESC
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_unacknowledged_risk_events")
    op.execute("DROP VIEW IF EXISTS v_current_positions")
    for t in [
        "orderbook_snapshots", "pnl_timeseries", "price_history",
        "funding_rate_history", "user_settings", "system_state",
        "notifications", "risk_events", "orders", "position_legs",
        "position_status_log", "positions", "opportunities",
        "strategy_instances",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS log_position_status_change() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS trigger_set_updated_at() CASCADE")
