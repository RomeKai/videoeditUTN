"""
The wallet's movements as the web app shows them (Uso › Movimientos, G1 and G2).

- ``kind``: what a Transaction did to the balance, from its type and status
  (``KINDS``). A row with no kind (a DEPOSIT or REFUND that FAILED, a SPEND
  still PENDING) never touched the balance and is not a movement.
- Months and days are those of ``settings.WORKSPACE_TIME_ZONE``.
- A row is dated when it last moved the balance (``MOVED_AT``): a charge or a
  return when it was settled (``settled_at``; ``created_at`` on rows settled
  before it existed), an open reservation or a purchase when it was made. So
  the list, its month filter, its totals and the usage chart put a charge in
  the same month.
- What was returned is the released reservations and, of a partial charge,
  ``reserved_amount - amount`` (commit_partial_reservation writes no row for it).
- The CSV is the ledger: one row per change of the balance (``ledger``), so
  its amounts add up to what the balance did in the period exported.

Amounts stay ``Decimal`` and go out as plain decimal text, never a float.
"""
import csv
import io
import operator
import re
from datetime import datetime, timedelta
from functools import reduce
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Case, CharField, Count, DecimalField, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce, TruncDate

from ..models import Transaction, Wallet

Type, Status, Category = Transaction.Type, Transaction.Status, Transaction.Category

# (type, status) -> kind. The order is the one of the filter chips.
KINDS = {
    (Type.DEPOSIT, Status.COMPLETED): 'purchase',
    (Type.SPEND, Status.RESERVED): 'reserved',
    (Type.SPEND, Status.COMPLETED): 'charged',
    (Type.REFUND, Status.COMPLETED): 'refund',
    # A reservation that was returned whole (rollback_reservation): "Devuelto".
    (Type.SPEND, Status.FAILED): 'released',
    (Type.DEPOSIT, Status.PENDING): 'pending',
    (Type.REFUND, Status.PENDING): 'pending',
}
KIND_CHOICES = list(dict.fromkeys(KINDS.values()))
# What the CSV says of each kind (front/src/features/uso/movements.tsx, KIND_META).
KIND_LABELS = {
    'purchase': 'Compra', 'reserved': 'Reservado', 'charged': 'Cobrado',
    'refund': 'Devolución', 'released': 'Devuelto', 'pending': 'Pendiente',
}

ZERO = Decimal('0E-10')
# When a row last moved the balance. The same expression as the index of
# migration 0015 (wallet, MOVED_AT desc): the first page of a wallet reads it.
MOVED_AT = Coalesce('settled_at', 'created_at')
CHARGED_AT = MOVED_AT
# A partial charge: it kept what was charged and gave the rest back.
PARTIAL = Q(transaction_type=Type.SPEND, status=Status.COMPLETED, reserved_amount__gt=F('amount'))


def time_zone():
    return ZoneInfo(settings.WORKSPACE_TIME_ZONE)


def kind_of(txn):
    return KINDS.get((txn.transaction_type, txn.status))


def kind_filter(kind=None):
    """Q of the rows of ``kind`` (one of KIND_CHOICES); without one, of every row that is a movement."""
    return reduce(operator.or_, (
        Q(transaction_type=transaction_type, status=status)
        for (transaction_type, status), value in KINDS.items() if kind is None or value == kind
    ))


def kind_annotation():
    return Case(
        *(When(transaction_type=t, status=s, then=Value(kind)) for (t, s), kind in KINDS.items()),
        output_field=CharField(),
    )


def money(value):
    """A Decimal as JSON text: exact, never in exponent notation."""
    return format(Decimal(value), 'f')


def csv_amount(value):
    """Two decimals when that is exact, else every decimal it has: ``-555.30``, ``0.1234567``."""
    value = Decimal(value)
    cents = value.quantize(Decimal('0.01'))
    return format(cents if cents == value else value.normalize(), 'f')


def parse_month(raw):
    """``(year, month)`` of a ``YYYY-MM``, or None when it is not one."""
    match = re.fullmatch(r'([0-9]{4})-([0-9]{2})', raw or '')
    if match is None:
        return None
    year, month = int(match[1]), int(match[2])
    # 9999-12 has no next month to end it.
    if not 1 <= month <= 12 or not 1 <= year <= 9998:
        return None
    return year, month


def current_month(now):
    local = now.astimezone(time_zone())
    return local.year, local.month


def month_bounds(year, month):
    """First instant of the month and of the next one, in the workspace's zone."""
    zone = time_zone()
    start = datetime(year, month, 1, tzinfo=zone)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=zone)
    return start, end


def wallet_id_of(workspace):
    """The id of the workspace's wallet, or None. The movements filter by it, not through a join."""
    return Wallet.objects.filter(workspace_id=workspace.pk).values_list('pk', flat=True).first()


def movements(wallet_id, *, kind=None, month=None, project=None):
    """
    The wallet's movements with the filters, newest first by MOVED_AT.
    ``month``: ``(year, month)``, by MOVED_AT too.
    """
    if wallet_id is None:
        return Transaction.objects.none()
    rows = Transaction.objects.filter(kind_filter(kind), wallet_id=wallet_id).annotate(moved_at=MOVED_AT)
    if month is not None:
        start, end = month_bounds(*month)
        rows = rows.filter(moved_at__gte=start, moved_at__lt=end)
    if project is not None:
        rows = rows.filter(project_id=project)
    return rows.order_by('-moved_at', '-id')


def with_details(rows):
    """What the serializer reads of the creator and the project, in the same query."""
    return rows.select_related('created_by', 'project').only(
        'id', 'wallet_id', 'created_by_id', 'amount', 'reserved_amount', 'transaction_type', 'status', 'description',
        'created_at', 'settled_at', 'category', 'project_id', 'project__title',
        'created_by__first_name', 'created_by__last_name', 'created_by__username',
    )


def totals(rows):
    """
    ``{kind: {count, amount}}`` of ``rows``, only the kinds that have
    movements. ``released`` also adds what the partial charges gave back
    (its ``partial_count`` says how many): ``count`` is still the released
    rows, the ones its filter lists. One query.
    """
    money_field = DecimalField(max_digits=30, decimal_places=10)
    grouped = (
        rows.order_by().annotate(kind=kind_annotation()).values('kind')
        .annotate(
            # Not named like the fields: an alias "amount" would hide the column from the F() below.
            rows_count=Count('id'),
            rows_amount=Sum('amount'),
            partial_count=Count('id', filter=PARTIAL),
            partial_amount=Sum(F('reserved_amount') - F('amount'), filter=PARTIAL, output_field=money_field),
        )
    )
    result, partial_count, partial_amount = {}, 0, ZERO
    for row in grouped:
        if not row['kind']:
            continue
        result[row['kind']] = {'count': row['rows_count'], 'amount': row['rows_amount']}
        partial_count += row['partial_count']
        partial_amount += row['partial_amount'] or ZERO
    if partial_count or 'released' in result:
        released = result.get('released', {'count': 0, 'amount': ZERO})
        result['released'] = {**released, 'amount': released['amount'] + partial_amount, 'partial_count': partial_count}
    return {kind: {**entry, 'amount': money(entry['amount'])} for kind, entry in result.items()}


def charged(wallet_id):
    """What was charged to the wallet, with ``charged_at`` (when: MOVED_AT)."""
    return Transaction.objects.filter(
        wallet_id=wallet_id, transaction_type=Type.SPEND, status=Status.COMPLETED,
    ).annotate(charged_at=CHARGED_AT)


def spent_in_month(wallet_id, year, month):
    start, end = month_bounds(year, month)
    total = charged(wallet_id).filter(charged_at__gte=start, charged_at__lt=end).aggregate(total=Sum('amount'))['total']
    return total if total is not None else ZERO


def usage(wallet_id, year, month):
    """Every day of the month: what was charged, split in projects, renders and SEO."""
    start, end = month_bounds(year, month)
    per_day = (
        charged(wallet_id).filter(charged_at__gte=start, charged_at__lt=end)
        .annotate(day=TruncDate('charged_at', tzinfo=time_zone()))
        .order_by().values('day', 'category').annotate(total=Sum('amount'))
    )
    columns = {Category.RENDER: 'renders', Category.SEO: 'seo'}
    days = {}
    for row in per_day:
        entry = days.setdefault(row['day'], {'projects': ZERO, 'renders': ZERO, 'seo': ZERO})
        column = columns.get(row['category'], 'projects')
        entry[column] += row['total']
    result = []
    day = start.date()
    while day < end.date():
        entry = days.get(day, {'projects': ZERO, 'renders': ZERO, 'seo': ZERO})
        result.append({'date': day.isoformat(), **{key: money(value) for key, value in entry.items()}})
        day += timedelta(days=1)
    return result


# A cell a spreadsheet would run as a formula (CSV injection).
_FORMULA_START = ('=', '+', '-', '@', '\t', '\r')


def _text_cell(value):
    return f"'{value}" if value.startswith(_FORMULA_START) else value


CSV_HEADER = ['fecha', 'concepto', 'proyecto', 'tipo', 'monto', 'estado', 'transaccion']
# What each change of the balance is called in the CSV.
RESERVATION, RETURN, PURCHASE, REFUND = 'Reserva', 'Devuelto', 'Compra', 'Devolución'


def balance_changes(txn):
    """
    ``(when, what, amount)`` of each time ``txn`` moved the available balance:

    - a SPEND took what it reserved when it was made (``reserved_amount``;
      ``amount`` on rows from before migration 0012), and gave back, when it
      was settled, all of it (FAILED) or what was not charged (a partial charge);
    - a purchase and a refund gave their amount.

    A charge itself moves nothing more (it was reserved), and a pending
    purchase has not moved anything yet.
    """
    settled = txn.settled_at or txn.created_at
    if txn.transaction_type == Type.SPEND:
        reserved = txn.reserved_amount if txn.reserved_amount is not None else txn.amount
        yield txn.created_at, RESERVATION, -reserved
        if txn.status == Status.FAILED:
            yield settled, RETURN, reserved
        elif txn.status == Status.COMPLETED and reserved > txn.amount:
            yield settled, RETURN, reserved - txn.amount
    elif txn.status == Status.COMPLETED:
        yield settled, PURCHASE if txn.transaction_type == Type.DEPOSIT else REFUND, txn.amount


def ledger(wallet_id, *, kind=None, month=None, project=None):
    """
    The changes of the balance of the wallet's movements (the filters of
    ``movements``: ``kind`` and ``project`` choose the transactions), newest
    first. With ``month``, only the changes made in it: their amounts add up
    to what the balance did that month.
    """
    if wallet_id is None:
        return []
    rows = Transaction.objects.filter(kind_filter(kind), wallet_id=wallet_id)
    start = end = None
    if month is not None:
        start, end = month_bounds(*month)
        rows = rows.filter(
            Q(created_at__gte=start, created_at__lt=end) | Q(settled_at__gte=start, settled_at__lt=end)
        )
    if project is not None:
        rows = rows.filter(project_id=project)
    changes = []
    for txn in with_details(rows).order_by().iterator(chunk_size=1000):
        for order, (at, what, amount) in enumerate(balance_changes(txn)):
            if start is None or start <= at < end:
                changes.append((at, order, txn, what, amount))
    # Newest first; a reservation and its return at the same instant keep their order.
    changes.sort(key=lambda change: (change[0], change[1], str(change[2].pk)), reverse=True)
    return changes


def csv_text(changes):
    """
    The ledger as CSV, one change of the balance per row: when (in the
    workspace's zone), the description, the project, what it was (``Reserva``,
    ``Devuelto``, ``Compra``, ``Devolución``), the amount with its sign, the
    state of its transaction now («Cobrado», «Devuelto»...) and its id, which
    pairs a reservation with its return.
    """
    zone = time_zone()
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(CSV_HEADER)
    for at, _order, txn, what, amount in changes:
        writer.writerow([
            at.astimezone(zone).isoformat(timespec='seconds'),
            _text_cell(txn.description),
            _text_cell(txn.project.title) if txn.project_id else '',
            what,
            csv_amount(amount),
            KIND_LABELS[kind_of(txn)],
            str(txn.pk),
        ])
    return out.getvalue()
