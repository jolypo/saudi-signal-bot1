import argparse
import asyncio
import os

import uvicorn

from app.config.settings import settings

from app.data.providers.sahmk import SahmkProvider
from app.data.providers.yahoo import YahooHistoricalProvider
from app.data.tasilab import TasilabProvider
from app.data.provider_router import ProviderRouter

from app.telegram.bots import TelegramBots
from app.service import TradingService
from app.scheduler.runner import Scheduler
from app.web import app, configure


def _print_tasilab_diagnostic(report):
    print("\n====================================")
    print("TASILAB DIAGNOSTIC")
    print("====================================")
    print("classification:", report.get("classification", "UNKNOWN"))
    print("base_url:", report.get("base_url", ""))
    print("symbol:", report.get("symbol", ""))
    for name, item in report.get("checks", {}).items():
        print(
            f"[{name}] ok={item.get('ok')} "
            f"status={item.get('status')} "
            f"latency={item.get('latency_ms')}ms "
            f"cloudflare={item.get('cloudflare')} "
            f"retry_after={item.get('retry_after') or '-'}"
        )
        if item.get("cf_ray"):
            print(f"[{name}] cf-ray={item.get('cf_ray')}")
        if item.get("body_preview"):
            print(f"[{name}] detail={item.get('body_preview')}")
    print("====================================")


# =========================================================
# WEB SERVER
# =========================================================

async def run_web():
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "10000",
            )
        ),
        log_level="info",
    )

    server = uvicorn.Server(
        config
    )

    await server.serve()


# =========================================================
# MAIN
# =========================================================

async def main(args):

    # =====================================================
    # SAHMK PROVIDER
    # =====================================================

    sahmk_provider = SahmkProvider(
        settings.sahmk_api_key,
        settings.sahmk_base_url,
        min_request_interval=(
            settings.sahmk_min_request_interval
        ),
        local_daily_request_limit=(
            settings.sahmk_local_daily_limit
        ),
        timezone_name=(
            settings.timezone
        ),
    )

    # =====================================================
    # TASILAB PROVIDER
    # =====================================================

    tasilab_provider = TasilabProvider(
        settings
    )

    # =====================================================
    # SMART PROVIDER ROUTER
    # =====================================================

    provider = ProviderRouter(
        settings=settings,
        sahmk_provider=sahmk_provider,
        tasilab_provider=tasilab_provider,
    )

    # =====================================================
    # HISTORICAL PROVIDER
    # =====================================================

    historical = YahooHistoricalProvider()

    # =====================================================
    # TELEGRAM
    # =====================================================

    bots = TelegramBots(
        settings
    )

    scheduler_task = None

    try:

        # =================================================
        # TELEGRAM TEST
        # =================================================

        if args.test_telegram:

            await bots.test()

            print(
                "[test] Telegram "
                "connection test passed"
            )

            return

        # =================================================
        # TASILAB DIAGNOSTIC TEST
        # =================================================

        if args.test_tasilab:
            report = await tasilab_provider.diagnose("1120")
            _print_tasilab_diagnostic(report)
            return

        # =================================================
        # DATA TEST
        # =================================================

        if args.test_data:

            print(
                "\n"
                "===================================="
            )

            print(
                "DATA PROVIDER TEST"
            )

            print(
                "===================================="
            )

            # ---------------------------------------------
            # TASILAB HEALTH MATRIX
            # ---------------------------------------------

            try:
                report = await tasilab_provider.diagnose("1120")
                _print_tasilab_diagnostic(report)
            except Exception as exc:
                print(
                    "[test] Tasilab diagnostic failed:",
                    exc,
                )

            # ---------------------------------------------
            # COMPANIES TEST
            # ---------------------------------------------

            try:

                companies = await provider.companies(
                    "TASI"
                )

                print(
                    "[test] TASI companies:",
                    len(companies),
                )

            except Exception as exc:

                print(
                    "[test] companies failed:",
                    exc,
                )

            # ---------------------------------------------
            # MARKET SUMMARY TEST
            # ---------------------------------------------

            try:

                summary = await provider.market_summary()

                print(
                    "[test] market summary:",
                    summary,
                )

            except Exception as exc:

                print(
                    "[test] market summary failed:",
                    exc,
                )

            # ---------------------------------------------
            # QUOTE TEST
            # ---------------------------------------------

            try:

                quote = await provider.quote(
                    "1120"
                )

                print(
                    "[test] 1120 quote:",
                    quote.price,
                )

            except Exception as exc:

                print(
                    "[test] 1120 quote failed:",
                    exc,
                )

            # ---------------------------------------------
            # TOP VOLUME TEST
            # ---------------------------------------------

            try:

                top_volume = (
                    await provider.top_volume_quotes(
                        10,
                        "TASI",
                    )
                )

                print(
                    "[test] top volume:",
                    len(top_volume),
                )

            except Exception as exc:

                print(
                    "[test] top volume failed:",
                    exc,
                )

            # ---------------------------------------------
            # ROUTER STATS
            # ---------------------------------------------

            try:

                print(
                    "[test] router stats:",
                    provider.stats(),
                )

            except Exception as exc:

                print(
                    "[test] stats failed:",
                    exc,
                )

            print(
                "===================================="
            )

            return

        # =================================================
        # TRADING SERVICE
        # =================================================

        service = TradingService(
            settings,
            provider,
            bots,
            historical_provider=historical,
        )

        # =================================================
        # WEB APP
        # =================================================

        configure(
            service,
            bots,
        )

        # =================================================
        # TELEGRAM COMMANDS
        # =================================================

        await bots.start_commands()

        print(
            "[main] service + Telegram "
            f"{bots.mode} started"
        )

        print(
            "[main] market provider router enabled"
        )

        print(
            "[main] primary provider: SAHMK"
        )

        print(
            "[main] secondary provider: Tasilab"
        )

        print(
            "[main] SAHMK daily switch limit: "
            f"{settings.sahmk_daily_switch_limit}"
        )

        # =================================================
        # SCHEDULER
        # =================================================

        scheduler_task = asyncio.create_task(
            Scheduler(
                settings,
                service,
            ).run()
        )

        # =================================================
        # WEB SERVER
        # =================================================

        await run_web()

    finally:

        # =================================================
        # STOP SCHEDULER
        # =================================================

        if scheduler_task is not None:

            scheduler_task.cancel()

            await asyncio.gather(
                scheduler_task,
                return_exceptions=True,
            )

        # =================================================
        # STOP TELEGRAM
        # =================================================

        try:

            await bots.stop_commands()

        finally:

            # =============================================
            # CLOSE SAHMK
            # =============================================

            try:

                await sahmk_provider.close()

            except Exception as exc:

                print(
                    "[shutdown] SAHMK close "
                    f"failed: {exc}"
                )

            # =============================================
            # CLOSE TASILAB
            # =============================================

            try:

                await tasilab_provider.close()

            except Exception as exc:

                print(
                    "[shutdown] Tasilab close "
                    f"failed: {exc}"
                )

            # =============================================
            # CLOSE YAHOO HISTORICAL
            # =============================================

            try:

                await historical.close()

            except Exception as exc:

                print(
                    "[shutdown] Yahoo close "
                    f"failed: {exc}"
                )


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--test-telegram",
        action="store_true",
    )

    parser.add_argument(
        "--test-data",
        action="store_true",
    )

    parser.add_argument(
        "--test-tasilab",
        action="store_true",
    )

    asyncio.run(
        main(
            parser.parse_args()
        )
    )
