"""
Pipeline metrics for the operations dashboard (INFRA-02).

    python manage.py pipeline_metrics            # last 24 h, table
    python manage.py pipeline_metrics --hours 168 --json

Durations per stage (p50 / p95 / max), projects per status, failure rate and
failures per static error code. See apps.videos.services.pipeline_metrics.
"""

import json

from django.core.management.base import BaseCommand, CommandError

from apps.videos.services.pipeline_metrics import DEFAULT_WINDOW_HOURS, collect


def _fmt(value):
    return "-" if value is None else f"{value:.1f}"


class Command(BaseCommand):
    help = "Métricas del pipeline: duración por etapa, estados y fallas (INFRA-02)."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=DEFAULT_WINDOW_HOURS,
                            help="Ventana en horas hacia atrás (default 24).")
        parser.add_argument("--json", action="store_true", help="Salida JSON en vez de tabla.")

    def handle(self, *args, hours, json: bool = False, **options):
        if hours <= 0:
            raise CommandError("--hours debe ser mayor que 0.")
        metrics = collect(hours=hours)
        if json:
            self.stdout.write(_dumps(metrics))
            return

        out = self.stdout
        out.write(f"Ventana: últimas {hours} h ({metrics['window']['since']} → {metrics['window']['until']})")
        out.write("")
        out.write(f"{'Etapa':<18}{'N':>6}{'p50 s':>10}{'p95 s':>10}{'máx s':>10}")
        for stage, row in metrics["stages"].items():
            out.write(f"{stage:<18}{row['count']:>6}{_fmt(row['p50_s']):>10}"
                      f"{_fmt(row['p95_s']):>10}{_fmt(row['max_s']):>10}")
        if not metrics["stages"]:
            out.write("(sin etapas registradas en la ventana)")
        out.write("")
        out.write("Proyectos por estado: " + (", ".join(
            f"{status}={n}" for status, n in sorted(metrics["projects"].items())) or "ninguno"))
        rate = metrics["failure_rate"]
        out.write("Tasa de falla: " + ("-" if rate is None else f"{rate * 100:.1f} %"))
        if metrics["failures"]:
            out.write("Fallas por código: " + ", ".join(
                f"{code}={n}" for code, n in metrics["failures"].items()))


def _dumps(data):
    return json.dumps(data, ensure_ascii=False, indent=2)
