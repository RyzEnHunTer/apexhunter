"""
UI Renderer Module - Institutional Rich Terminal Interface
==========================================================
Location: core/ui_renderer.py

Provides institutional double-border control consoles, live telemetry dashboards,
economic calendar tables, and Golden Numbers spread monitoring tables.
Zero external repository dependencies.
"""

from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

console = Console(width=92, force_terminal=True, legacy_windows=False)


def render_institutional_control_center(
    login: str,
    server: str,
    broker_company: str,
    balance: float,
    equity: float,
    free_margin: float,
    day_start: float,
    daily_cb_pct: float,
    daily_room: float,
    floor_cb_bal: float,
    floor_room: float,
    news_events_count: int,
    spread_caps: Dict[str, float],
    account_cfg: Optional[Dict[str, Any]] = None,
    target_info: Optional[Dict[str, Any]] = None
):
    """Renders the Master Institutional Command Control Center upon bot launch."""
    console.print("\n")
    # 1. Institutional Double-Box Header
    console.print(Panel(
        f"[bold bright_white]MASTER INSTITUTIONAL UNIFIED TRADING STATION[/]\n"
        f"[bright_cyan]Simultaneous Dual-Engine Execution[/] [dim white]| DCC ApexHunter v1.2.1 + Lewis Kelly SMC Basket[/]",
        style="bold bright_cyan",
        border_style="bright_blue",
        box=box.DOUBLE
    ))

    # 2. Account & Connection Status Card
    pnl_dollar = equity - day_start
    pnl_pct = (pnl_dollar / day_start) * 100.0 if day_start > 0 else 0.0
    pnl_style = "bold bright_green" if pnl_dollar >= 0 else "bold bright_red"
    pnl_sign = "+$" if pnl_dollar >= 0 else "-$"

    lifecycle = account_cfg.get("account_lifecycle", "challenge").upper() if account_cfg else "CHALLENGE"
    curr_phase = account_cfg.get("current_phase", 1) if account_cfg else 1
    ch_steps = account_cfg.get("challenge_steps", 2) if account_cfg else 2
    hwm = account_cfg.get("high_water_mark", equity) if account_cfg else equity
    lifecycle_badge = f"[bold bright_yellow]CHALLENGE ({ch_steps}-Step | Phase {curr_phase})[/]" if lifecycle == "CHALLENGE" else "[bold bright_green]FUNDED (Profit Share)[/]"

    notif_plat = account_cfg.get("notifications", {}).get("active_platform", "none").upper() if account_cfg else "DISCORD"
    if notif_plat in ("ALL", "BOTH"):
        notif_badge = "[bold bright_green]DUAL (Telegram + Discord)[/]"
    elif notif_plat == "TELEGRAM":
        notif_badge = "[bold bright_green]TELEGRAM ACTIVE[/]"
    elif notif_plat == "DISCORD":
        notif_badge = "[bold bright_green]DISCORD ACTIVE[/]"
    else:
        notif_badge = "[dim white]MUTED (Off)[/]"

    acc_table = Table(box=box.ROUNDED, border_style="bright_blue", expand=True)
    acc_table.add_column("Broker Connection & Lifecycle", style="bright_cyan", width=40)
    acc_table.add_column("Capital & Equity Status", style="bold white", width=42)

    acc_table.add_row(
        f"Broker:    [bold white]{broker_company}[/]\nLogin:     [bold white]{login}[/] ([bright_green]{server}[/])\nLifecycle: {lifecycle_badge}\nAlerts:    {notif_badge}",
        f"Balance:   [bold bright_green]${balance:,.2f}[/] (Free Margin: ${free_margin:,.2f})\n"
        f"Equity:    [bold bright_green]${equity:,.2f}[/] (Peak HWM: ${hwm:,.2f})\n"
        f"Today:     [{pnl_style}]{pnl_sign}{abs(pnl_dollar):,.2f} ({pnl_pct:+.2f}%)[/]"
    )
    console.print(acc_table)

    # 3. Prop Firm Milestone & Evaluation Progress Card
    if target_info:
        milestone_table = Table(box=box.ROUNDED, border_style="bright_blue", title="[bold bright_white]PROP FIRM TARGET MILESTONE & EVALUATION PROGRESS[/]", expand=True)
        milestone_table.add_column("Evaluation Metric", style="bright_cyan", width=22)
        milestone_table.add_column("Configuration & Live Progress", style="white", width=42)
        milestone_table.add_column("Milestone Status", justify="right", style="bold", width=18)

        if target_info.get("lifecycle") == "CHALLENGE":
            phase_num = target_info.get("phase", 1)
            start_b = target_info.get("start_balance", balance)
            tgt_pct = target_info.get("active_target_pct", 8.0)
            tgt_dollar = target_info.get("target_dollar", start_b * 1.08)
            p_dollar = target_info.get("profit_dollar", 0.0)
            p_pct = target_info.get("profit_pct", 0.0)
            rem_dollar = target_info.get("remaining_dollar", 0.0)
            is_reached = target_info.get("is_reached", False)
            is_locked = target_info.get("target_locked", False)

            bar_len = 12
            ratio = min(1.0, max(0.0, p_pct / tgt_pct)) if tgt_pct > 0 else 0.0
            filled = int(bar_len * ratio)
            p_sign = "+$" if p_dollar >= 0 else "-$"
            p_col = "bold bright_green" if p_dollar >= 0 else "bold bright_red"

            if is_reached or is_locked:
                p_bar = f"[bold bright_green]{'=' * bar_len}[/]"
                status_badge = "[bold bright_green]PASSED / LOCKED[/]"
                rem_desc = "[bold bright_green]Target Reached[/]"
            else:
                p_bar = f"[bright_cyan]{'=' * filled}[/][white]{'-' * (bar_len - filled)}[/]"
                status_badge = f"[bold bright_cyan]{ratio*100.0:.0f}%[/] [dim white](${rem_dollar:,.2f} left)[/]"
                rem_desc = f"[bold yellow]+${rem_dollar:,.2f} left[/]"

            milestone_table.add_row(
                f"Phase {phase_num} Target Goal",
                f"Goal: [bold white]+{tgt_pct:.1f}%[/] (${tgt_dollar:,.2f}) | Anchor: [dim white]${start_b:,.2f}[/]",
                status_badge
            )
            milestone_table.add_row(
                "Closed Balance PnL",
                f"[{p_bar}] [{p_col}]{p_sign}{abs(p_dollar):,.2f} ({p_pct:+.2f}%)[/]",
                rem_desc
            )
        else:
            start_b = target_info.get("start_balance", balance)
            p_dollar = target_info.get("profit_dollar", 0.0)
            p_pct = target_info.get("profit_pct", 0.0)
            p_sign = "+$" if p_dollar >= 0 else "-$"
            p_col = "bold bright_green" if p_dollar >= 0 else "bold bright_red"
            milestone_table.add_row(
                "Funded Target Goal",
                "Continuous Capital Compounding & Profit Share Mode (No Ceiling)",
                "[bold bright_green]FUNDED[/]"
            )
            milestone_table.add_row(
                "Closed Balance PnL",
                f"Anchor: ${start_b:,.2f} | Net: [{p_col}]{p_sign}{abs(p_dollar):,.2f} ({p_pct:+.2f}%)[/]",
                "[bold bright_green]PROFIT SHARE[/]"
            )

        use_custom = account_cfg.get("use_custom_phase_risk", False) if account_cfg else False
        active_r = (account_cfg.get("risk_per_trade", 0.01) * 100.0) if account_cfg else 1.00
        scaling_str = f"[bold cyan]ON ({active_r:.2f}%)[/]" if use_custom else f"[dim white]OFF (Fixed {active_r:.2f}%)[/]"
        milestone_table.add_row(
            "Phase Risk Scaling",
            f"Active Risk: [bold white]{active_r:.2f}%[/] per trade | Mode: {scaling_str}",
            "[bold bright_green]ACTIVE RISK[/]"
        )
        console.print(milestone_table)

    # 4. Safeguards & Circuit Breakers Card
    cb_table = Table(box=box.ROUNDED, border_style="bright_blue", title="[bold bright_white]RISK SAFEGUARDS & CIRCUIT BREAKER BUFFERS[/]", expand=True)
    cb_table.add_column("Safeguard Engine", style="bright_cyan", width=22)
    cb_table.add_column("Configured Rule / Floor Threshold", style="white", width=40)
    cb_table.add_column("Safe Room Buffer", style="bold", justify="right", width=18)

    cb_table.add_row(
        "Daily Circuit Breaker",
        f"{daily_cb_pct*100:.1f}% loss from 00:00 UTC anchor (${day_start:,.2f})",
        f"[bold bright_green]+${daily_room:,.2f} safe[/]" if daily_room > 0 else "[bold bright_red]$0.00 (HALTED)[/]"
    )
    max_cb_pct = account_cfg.get("max_cb_pct", 8.5) if account_cfg else 8.5
    cb_table.add_row(
        "Max Floor CB",
        f"{max_cb_pct:.1f}% Floor (${floor_cb_bal:,.2f}) | 0.20% Recovery Zone",
        f"[bold bright_green]+${floor_room:,.2f} safe[/]" if floor_room > 0 else "[bold bright_red]$0.00 (HALTED)[/]"
    )
    cb_table.add_row(
        "News Guardian",
        "10m Pre-News Close | 20m/15m Embargo | 12h Sync",
        f"[bold bright_cyan]ACTIVE ({news_events_count} Ev)[/]"
    )
    cb_table.add_row(
        "Spread Guardian",
        "Golden Numbers Caps + Relative 30% SL Distance",
        "[bold bright_cyan]FILTER ACTIVE[/]"
    )
    console.print(cb_table)

    # 5. Monitored Strategy Engines Strip
    strat_table = Table(box=box.ROUNDED, border_style="bright_blue", title="[bold bright_white]SIMULTANEOUS STRATEGY ENGINES (NO SWITCHER)[/]", expand=True)
    strat_table.add_column("Engine", style="bold bright_cyan", width=22)
    strat_table.add_column("Designated Assets & Timeframes", style="white", width=38)
    strat_table.add_column("Execution Setup", style="dim white", width=20)

    strat_table.add_row(
        "DCC ApexHunter v1.2.1",
        "Gold (XAUUSD) & Nasdaq (NAS100) on M5",
        "2-Way Split (1.4R, 2.1R)"
    )
    strat_table.add_row(
        "Lewis Kelly SMC Basket",
        "6 Pairs (EUR, GBP, USD, AUD, JPY, CAD) on M15",
        "3-Way Split (2.5R, 5R, 8R)"
    )
    console.print(strat_table)

    # 6. Action Command Palette
    action_table = Table(box=box.ROUNDED, border_style="bright_blue", title="[bold bright_white]BOT CONTROL ACTION PALETTE[/]", expand=True)
    action_table.add_column("Key", justify="center", width=8)
    action_table.add_column("Action & Mode Description", style="white")

    action_table.add_row("[bold bright_green][1][/]", "[bold bright_green]Start LIVE Order Execution[/]     [white]— Real MT5 orders, fitted lot sizing, server-side splits[/]")
    action_table.add_row("[bold bright_cyan][2][/]", "[bold bright_cyan]Start DRY-RUN Paper Mode[/]      [white]— Forward test on live market ticks (Zero risk)[/]")
    action_table.add_row("[bold bright_yellow][3][/]", "[bold bright_yellow]Manage Lifecycle, DD & CB[/] [white]— Prop firm phases, targets, DD limits & CB floors[/]")
    action_table.add_row("[bold bright_magenta][4][/]", "[bold bright_magenta]Manage Notification Engine[/]    [white]— Setup Telegram Bot / Discord Webhook credentials & test[/]")
    action_table.add_row("[bold yellow][5][/]", "[bold yellow]View Economic Calendar[/]        [white]— Inspect Forex Factory high-impact events & countdowns[/]")
    action_table.add_row("[bold yellow][6][/]", "[bold yellow]View Spread Caps[/]              [white]— Inspect Golden Caps & live broker spreads across all 8 assets[/]")
    action_table.add_row("[bold magenta][7][/]", "[bold magenta]Send Test Remote Alert[/]        [white]— Verify active Telegram / Discord notification channels[/]")
    action_table.add_row("[bold bright_cyan][8][/]", "[bold bright_cyan]Run Strategy Diagnostics[/]     [white]— Run 100% mathematical test suite on ApexHunter & LK SMC engines[/]")
    action_table.add_row("[bold bright_red][9][/]", "[bold bright_red]Exit Bot[/]                      [white]— Disconnect MT5 and terminate session[/]")

    console.print(action_table)


def render_notifications_menu(acc_id: str, notif_cfg: Dict[str, Any]):
    """Renders interactive remote notifications setup console for Telegram and Discord."""
    plat = notif_cfg.get("active_platform", "none").lower()

    tg_tok = notif_cfg.get("telegram_bot_token", "")
    tg_tok_prev = f"{tg_tok[:10]}...{tg_tok[-4:]}" if len(tg_tok) > 14 else (tg_tok if tg_tok else "[dim white](not configured)[/]")
    tg_chat = notif_cfg.get("telegram_chat_id", "")
    tg_chat_prev = tg_chat if tg_chat else "[dim white](not configured)[/]"

    discord_url = notif_cfg.get("discord_webhook_url", "")
    discord_prev = f"{discord_url[:35]}...{discord_url[-6:]}" if len(discord_url) > 42 else (discord_url if discord_url else "[dim white](not configured)[/]")

    if plat in ("all", "both"):
        plat_badge = "[bold bright_green]DUAL ALERTS ACTIVE (Telegram + Discord)[/]"
    elif plat == "telegram":
        plat_badge = "[bold bright_green]TELEGRAM BOT ACTIVE[/]"
    elif plat == "discord":
        plat_badge = "[bold bright_green]DISCORD WEBHOOK ACTIVE[/]"
    else:
        plat_badge = "[bold yellow]MUTED (Standby / Off)[/]"

    table = Table(box=box.ROUNDED, border_style="bright_blue", title=f"[bold bright_white]REMOTE NOTIFICATIONS SETUP — ACCOUNT {acc_id}[/]", expand=True)
    table.add_column("Channel / Platform", style="bright_cyan", width=22)
    table.add_column("Current Status", width=24)
    table.add_column("Configured Endpoint / Credentials Preview", style="white")

    tg_status = "[bold bright_green]ACTIVE[/]" if plat in ("all", "both", "telegram") and tg_tok else ("[white]Configured (Standby)[/]" if tg_tok else "[dim white]Not Configured[/]")
    dc_status = "[bold bright_green]ACTIVE[/]" if plat in ("all", "both", "discord") and discord_url else ("[white]Configured (Standby)[/]" if discord_url else "[dim white]Not Configured[/]")

    table.add_row("Active Mode", plat_badge, "Alerts dispatched on trade entry, breakeven, exits & circuit breakers")
    table.add_row("Telegram Bot", tg_status, f"Token: {tg_tok_prev} | Chat ID: {tg_chat_prev}")
    table.add_row("Discord Webhook", dc_status, f"URL: {discord_prev}")
    console.print(table)

    menu = Table(box=box.ROUNDED, border_style="bright_blue", title="[bold bright_white]Notification Options[/]", expand=True)
    menu.add_column("Key", justify="center", width=8)
    menu.add_column("Action & Details", style="white")

    menu.add_row("[bold bright_red][1][/]", "[bold white]Disable Notifications[/] [dim white]— Mute all Telegram & Discord alerts[/]")
    menu.add_row("[bold bright_cyan][2][/]", "[bold white]Configure Telegram Bot[/] [dim white]— Enter Bot Token & Chat ID + Send live test[/]")
    menu.add_row("[bold bright_cyan][3][/]", "[bold white]Configure Discord Webhook[/] [dim white]— Enter Webhook URL + Send live test[/]")
    menu.add_row("[bold bright_green][4][/]", "[bold white]Enable Dual Alerts (Both)[/] [dim white]— Dispatch to both Telegram & Discord simultaneously[/]")
    menu.add_row("[bold yellow][5][/]", "[bold white]Send Live Verification Test[/] [dim white]— Verify connection to currently active platform[/]")
    menu.add_row("[bold bright_cyan][6][/]", "[bold bright_cyan]Return to Main Menu[/] [dim white]— Go back to main command palette[/]")
    console.print(menu)


def render_lifecycle_menu(acc_id: str, cfg: Dict[str, Any], balance: float):
    """Renders interactive management menu for prop firm lifecycle, targets, and risk scaling."""
    l_curr = cfg.get("account_lifecycle", "challenge").upper()
    c_ph = cfg.get("current_phase", 1)
    ch_steps = cfg.get("challenge_steps", 2)

    status_str = f"CHALLENGE MODE ({ch_steps}-Step | Phase {c_ph})" if l_curr == "CHALLENGE" else "FUNDED ACCOUNT"
    use_scaling = cfg.get("use_custom_phase_risk", False)
    scaling_badge = "[bold bright_green]ENABLED[/]" if use_scaling else "[white]OFF (Fixed 1.00%)[/]"

    table = Table(box=box.ROUNDED, border_style="bright_blue", title=f"[bold bright_white]MANAGE LIFECYCLE & RISK — ACCOUNT {acc_id}[/]", expand=True)
    table.add_column("Parameter", style="bright_cyan", width=30)
    table.add_column("Current Setting", style="bold white")

    table.add_row("Lifecycle Status", f"[bold yellow]{status_str}[/]" if l_curr == "CHALLENGE" else "[bold bright_green]FUNDED MODE[/]")
    table.add_row("Phase Starting Balance", f"${cfg.get('phase_start_balance', balance):,.2f}")
    table.add_row("Phase 1 Evaluation Target", f"+{cfg.get('phase_1_target_pct', 8.0):.1f}%")
    if ch_steps > 1:
        table.add_row("Phase 2 Evaluation Target", f"+{cfg.get('phase_2_target_pct', 5.0):.1f}%")
    table.add_row("Challenge Risk Per Trade", f"{cfg.get('challenge_risk_pct', 1.25):.2f}%")
    table.add_row("Funded Risk Per Trade", f"{cfg.get('funded_risk_pct', 1.00):.2f}%")
    table.add_row("Active Risk Per Trade", f"{cfg.get('risk_per_trade', 0.01)*100.0:.2f}%")
    table.add_row("Custom Phase Risk Scaling", scaling_badge)

    d_cb = cfg.get("daily_cb_pct", 4.0)
    d_dd = cfg.get("daily_dd_limit_pct", 5.0)
    m_cb = cfg.get("max_cb_pct", 8.5)
    m_dd = cfg.get("max_total_dd_pct", 10.0)

    table.add_row("Daily Drawdown & CB", f"[bold bright_yellow]{d_cb:.2f}% CB[/] [dim white](Hard DD Limit: {d_dd:.2f}%)[/]")
    table.add_row("Max Total Drawdown & CB", f"[bold bright_yellow]{m_cb:.2f}% Floor CB[/] [dim white](Hard Breach Floor: {m_dd:.2f}%)[/]")
    table.add_row("Target Reached Lock", "[bold bright_green]LOCKED (Trading Halted)[/]" if cfg.get("target_locked") else "[white]Active (Trading Allowed)[/]")
    console.print(table)

    menu = Table(box=box.ROUNDED, border_style="bright_blue", title="[bold bright_white]Select Action[/]", expand=True)
    menu.add_column("Key", justify="center", width=8)
    menu.add_column("Action & Details", style="white")

    menu.add_row("[bold yellow][1][/]", "[bold white]Advance / Switch Active Phase[/] [dim white]— Phase 1 → Phase 2 → Funded mode[/]")
    menu.add_row("[bold yellow][2][/]", f"[bold white]Edit Challenge Risk %[/] [dim white]— Current: [bold cyan]{cfg.get('challenge_risk_pct', 1.25):.2f}%[/][/]")
    menu.add_row("[bold yellow][3][/]", f"[bold white]Edit Funded Risk %[/] [dim white]— Current: [bold cyan]{cfg.get('funded_risk_pct', 1.00):.2f}%[/][/]")
    menu.add_row("[bold yellow][4][/]", "[bold white]Edit Phase Targets[/] [dim white]— Overall combined % & phase ratio[/]")
    menu.add_row("[bold yellow][5][/]", f"[bold white]Reset Starting Balance[/] [dim white]— Current Anchor: [bold cyan]${cfg.get('phase_start_balance', balance):,.2f}[/][/]")
    menu.add_row("[bold yellow][6][/]", f"[bold white]Toggle Phase Risk Scaling[/] [dim white]— Currently: {'[bold bright_green]ON[/]' if use_scaling else '[white]OFF (Fixed 1.00%)[/]'}[/]")
    menu.add_row("[bold yellow][7][/]", f"[bold white]Edit Daily Drawdown & CB %[/] [dim white]— Current: [bold cyan]{d_cb:.2f}% CB / {d_dd:.2f}% Hard DD[/][/]")
    menu.add_row("[bold yellow][8][/]", f"[bold white]Edit Max Total DD & Floor CB %[/] [dim white]— Current: [bold cyan]{m_cb:.2f}% CB / {m_dd:.2f}% Hard Floor[/][/]")
    menu.add_row("[bold yellow][9][/]", f"[bold white]Unlock Target Lock[/] [dim white]— Current: {'[bold bright_green]LOCKED[/]' if cfg.get('target_locked') else '[white]UNLOCKED[/]'}[/]")
    menu.add_row("[bold bright_cyan][10][/]", "[bold bright_cyan]Return to Main Menu[/] [dim white]— Go back to main command palette[/]")
    console.print(menu)


def render_economic_calendar_table(events: List[Dict[str, Any]], hours_ahead: int = 48):
    """Renders upcoming high-impact economic calendar events."""
    now_utc = datetime.now(timezone.utc)
    cal_table = Table(
        title=f"UPCOMING HIGH-IMPACT ECONOMIC CALENDAR ({hours_ahead}H LOOKAHEAD)",
        title_style="bold bright_cyan",
        box=box.ROUNDED,
        border_style="bright_blue",
        header_style="bold bright_cyan",
        expand=True
    )
    cal_table.add_column("Time (UTC)", style="bold white", width=16)
    cal_table.add_column("Currency", justify="center", style="bold yellow", width=10)
    cal_table.add_column("Impact", justify="center", width=10)
    cal_table.add_column("Event Title", style="white", width=26)
    cal_table.add_column("Forecast", justify="right", style="cyan", width=10)
    cal_table.add_column("Previous", justify="right", style="white", width=10)

    if not events:
        console.print(Panel("[white]No high-impact economic events scheduled in the next 48 hours.[/]", title="Economic Calendar", border_style="bright_blue"))
        return

    for ev in events:
        cal_table.add_row(
            ev["time"].strftime("%Y-%m-%d %H:%M"),
            ev["currency"],
            "[bold bright_red]HIGH[/]",
            ev["title"],
            str(ev.get("forecast", "-")),
            str(ev.get("previous", "-"))
        )
    console.print(cal_table)


def render_live_spreads_table(spread_rows: List[Dict[str, Any]]):
    """Renders live broker spreads vs Golden Numbers caps across all 8 assets."""
    spread_table = Table(
        title="LIVE BROKER SPREADS VS GOLDEN CAPS (ALL 8 ASSETS)",
        title_style="bold bright_cyan",
        box=box.ROUNDED,
        border_style="bright_blue",
        header_style="bold bright_cyan",
        expand=True
    )
    spread_table.add_column("Symbol", style="bold white", width=9)
    spread_table.add_column("Broker Sym", style="dim white", width=11)
    spread_table.add_column("Bid", justify="right", width=11)
    spread_table.add_column("Ask", justify="right", width=11)
    spread_table.add_column("Live Spread", justify="center", width=12)
    spread_table.add_column("Golden Cap", justify="center", width=12)
    spread_table.add_column("Status", justify="center", width=14)

    for r in spread_rows:
        spread_table.add_row(
            r["symbol"],
            r["broker_sym"],
            r["bid_str"],
            r["ask_str"],
            r["spread_str"],
            r["cap_str"],
            r["status_str"]
        )
    console.print(spread_table)


def render_telemetry_dashboard(
    now_utc: datetime,
    login: str,
    server: str,
    balance: float,
    equity: float,
    day_start: float,
    daily_room: float,
    open_positions: Dict[str, Any],
    max_slots: int,
    news_events_count: int,
    asset_quotes: List[Dict[str, Any]],
    target_info: Optional[Dict[str, Any]] = None
):
    """Renders streaming telemetry dashboard every update cycle."""
    pnl_dollar = equity - day_start
    pnl_pct = (pnl_dollar / day_start) * 100.0 if day_start > 0 else 0.0
    pnl_style = "bold bright_green" if pnl_dollar >= 0 else "bold bright_red"
    pnl_sign = "+$" if pnl_dollar >= 0 else "-$"

    console.print("\n")
    # Top summary card
    summary_table = Table(box=box.ROUNDED, border_style="bright_blue", expand=True)
    summary_table.add_column("Account & Clock", style="bright_cyan", width=22)
    summary_table.add_column("Capital & Today PnL", style="bold white", width=23)
    summary_table.add_column("Guardians & Slots", style="bold", width=21)
    summary_table.add_column("Prop Firm Lifecycle", style="bold", justify="right", width=22)

    if target_info and target_info.get("lifecycle") == "CHALLENGE":
        phase_num = target_info.get("phase", 1)
        tgt_pct = target_info.get("active_target_pct", 8.0)
        rem_dollar = target_info.get("remaining_dollar", 0.0)
        is_hit = target_info.get("is_reached", False) or target_info.get("target_locked", False)
        status_line = "[bold bright_green]TARGET LOCKED[/]" if is_hit else f"[bold yellow]${rem_dollar:,.2f} left[/]"
        lifecycle_cell = f"[bold bright_yellow]CHALLENGE (Phase {phase_num})[/]\nGoal +{tgt_pct:.1f}% | {status_line}"
    else:
        lifecycle_cell = "[bold bright_green]FUNDED ACCOUNT[/]\nActive Profit Share"

    summary_table.add_row(
        f"Login: [bold white]{login}[/] ({server})\nTime:  [dim white]{now_utc.strftime('%H:%M:%S UTC')}[/]",
        f"Bal: [bold bright_green]${balance:,.2f}[/] | Eq: [bold bright_green]${equity:,.2f}[/]\n"
        f"Today: [{pnl_style}]{pnl_sign}{abs(pnl_dollar):,.2f} ({pnl_pct:+.2f}%)[/]",
        f"Daily Safe: [bold bright_green]${daily_room:,.2f}[/]\n"
        f"Slots: [bold bright_cyan]{len(open_positions)} / {max_slots}[/] ({news_events_count} Ev)",
        lifecycle_cell
    )
    console.print(summary_table)

    # Live asset streaming table
    asset_table = Table(box=box.ROUNDED, border_style="bright_blue", title="[bold bright_white]LIVE ASSET STREAMING & GUARDIAN MATRIX[/]", expand=True)
    asset_table.add_column("Symbol", style="bold bright_white", width=9)
    asset_table.add_column("Bid", justify="right", width=11)
    asset_table.add_column("Ask", justify="right", width=11)
    asset_table.add_column("Spread", justify="center", width=10)
    asset_table.add_column("Golden Cap", justify="center", width=12)
    asset_table.add_column("Strategy Engine", style="bright_cyan", width=19)
    asset_table.add_column("State", justify="center", width=11)

    for q in asset_quotes:
        asset_table.add_row(
            q["symbol"],
            q["bid"],
            q["ask"],
            q["spread"],
            q["cap"],
            q["strategy"],
            q["state"]
        )
    console.print(asset_table)

    # If any open positions exist, render active trade detail card
    if open_positions:
        pos_table = Table(box=box.ROUNDED, border_style="bright_green", title="[bold bright_white]ACTIVE PORTFOLIO POSITIONS[/]", expand=True)
        pos_table.add_column("Symbol", style="bold bright_white", width=9)
        pos_table.add_column("Strategy", style="bright_cyan", width=18)
        pos_table.add_column("Dir", justify="center", width=6)
        pos_table.add_column("Entry", justify="right", width=11)
        pos_table.add_column("Stop Loss", justify="right", width=11)
        pos_table.add_column("Breakeven SL", justify="right", width=11)
        pos_table.add_column("TP1 Hit", justify="center", width=8)

        for sym, pos in open_positions.items():
            pos_table.add_row(
                sym,
                pos.get("strategy", ""),
                pos.get("direction", ""),
                f"{pos.get('entry_price', 0.0):.5f}" if "USD" in sym and sym != "XAUUSD" else f"{pos.get('entry_price', 0.0):.2f}",
                f"{pos.get('sl', 0.0):.5f}" if "USD" in sym and sym != "XAUUSD" else f"{pos.get('sl', 0.0):.2f}",
                f"{pos.get('be_sl', 0.0):.5f}" if "USD" in sym and sym != "XAUUSD" else f"{pos.get('be_sl', 0.0):.2f}",
                "[bold bright_green]YES[/]" if pos.get("tp1_hit") else "[dim white]NO[/]"
            )
        console.print(pos_table)
