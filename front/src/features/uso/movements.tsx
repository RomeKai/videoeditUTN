import type { Transaction, TransactionKind, TransactionKindTotal } from '@/api/contracts/billing';
import { cx } from '@/lib/cx';
import { formatNumber } from '@/lib/format';
import { Badge, type BadgeTone } from '@/ui';
import styles from './uso.module.css';

export const MINUS = '−';

interface KindMeta {
  /** Singular, on the badge. */
  label: string;
  /** Plural, on the filter chip. */
  plural: string;
  tone: BadgeTone;
  /** Hollow dot: nothing happened to the balance yet. */
  hollow?: boolean;
  /** What the state means (tooltip of the badge). */
  help: string;
  /** Second line under the badge in the table. */
  note?: string;
  /** The amount adds to the balance. */
  income: boolean;
  amountClass?: string;
}

/** Map G2: how each `Transaction` state is shown. */
export const KIND_META: Record<TransactionKind, KindMeta> = {
  purchase: { label: 'Compra', plural: 'Compras', tone: 'outline', help: 'Tokens que compraste. Suman al saldo.', income: true, amountClass: styles.pos },
  reserved: {
    label: 'Reservado',
    plural: 'Reservados',
    tone: 'warn',
    help: 'Apartado para un trabajo en curso. Todavía no se cobró.',
    note: 'Se cobra al terminar',
    income: false,
    amountClass: styles.hold,
  },
  charged: { label: 'Cobrado', plural: 'Cobrados', tone: 'neutral', help: 'El trabajo terminó y se descontó del saldo.', income: false },
  refund: { label: 'Devolución', plural: 'Devoluciones', tone: 'ok', help: 'Te devolvimos tokens de algo ya cobrado.', income: true, amountClass: styles.pos },
  released: {
    label: 'Devuelto',
    plural: 'Devueltos',
    tone: 'ok',
    help: 'El trabajo falló antes de cobrarse; la reserva se liberó.',
    note: 'La reserva se liberó',
    income: true,
    amountClass: styles.pos,
  },
  pending: {
    label: 'Pendiente',
    plural: 'Pendientes',
    tone: 'outline',
    hollow: true,
    help: 'Esperando confirmación. No cambia el saldo todavía.',
    note: 'Todavía no suma al saldo',
    income: true,
    amountClass: styles.pend,
  },
};

export const KIND_ORDER: TransactionKind[] = ['purchase', 'reserved', 'charged', 'refund', 'released', 'pending'];

/** "+5.000" or "−1.404,90": the sign says what it does to the balance. */
export function signedAmount(amount: number, income: boolean): string {
  return `${income ? '+' : MINUS}${formatNumber(Math.abs(amount))}`;
}

export function KindBadge({ kind, size }: { kind: TransactionKind; size?: 'sm' | 'md' }) {
  const meta = KIND_META[kind];
  return (
    <Badge tone={meta.tone} dot={!meta.hollow} size={size} title={meta.help} className={meta.hollow ? styles.hollow : undefined}>
      {meta.label}
    </Badge>
  );
}

export function MovementAmount({ transaction, className }: { transaction: Pick<Transaction, 'amount' | 'kind'>; className?: string }) {
  const meta = KIND_META[transaction.kind];
  return <span className={cx('num', styles.amt, meta.amountClass, className)}>{signedAmount(transaction.amount, meta.income)}</span>;
}

/** When the movement last moved the balance: a charge or a return when it was settled. The list is ordered by it. */
export function movementDate(transaction: Pick<Transaction, 'created_at' | 'settled_at'>): string {
  return transaction.settled_at ?? transaction.created_at;
}

/** What a partial charge gave back (`reserved_amount − amount`); 0 for anything else. */
export function returnedPart(transaction: Pick<Transaction, 'kind' | 'amount' | 'reserved_amount'>): number {
  if (transaction.kind !== 'charged' || transaction.reserved_amount === null) return 0;
  return Math.max(0, Math.round((transaction.reserved_amount - transaction.amount) * 100) / 100);
}

export type KindTotals = Partial<Record<TransactionKind, TransactionKindTotal>>;

export function totalOf(totals: KindTotals | undefined, kind: TransactionKind): TransactionKindTotal {
  return totals?.[kind] ?? { count: 0, amount: 0 };
}

/** The description the API sends, without the redundant prefix when the project is linked below. */
export function movementConcept(transaction: Pick<Transaction, 'description' | 'project' | 'kind' | 'amount'>): string {
  if (transaction.project) {
    const title = transaction.project.title;
    const trimmed = transaction.description
      .replace(title, '')
      .replace(/\s+en\s*$/i, '')
      .replace(/[:·\s]+$/, '')
      .trim();
    return trimmed || 'Proyecto';
  }
  if (transaction.kind === 'purchase' || transaction.kind === 'pending') return `Compra de ${formatNumber(transaction.amount)} tokens`;
  return transaction.description;
}
