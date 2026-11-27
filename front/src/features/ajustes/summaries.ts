import type { SocialAccount } from '@/api/contracts/social';
import type { MembersResponse, SeatLimit } from '@/api/contracts/workspaces';
import { formatDate } from '@/lib/format';

/** "4 de 10 miembros · 1 invitación pendiente" (H2 header and the phone list). */
export function membersSummary(data: MembersResponse): string {
  const active = data.members.filter((member) => member.status === 'active').length;
  const pending = data.members.length - active;
  const base = `${active} de ${data.max_members} ${data.max_members === 1 ? 'miembro' : 'miembros'}`;
  return pending > 0 ? `${base} · ${pending} ${pending === 1 ? 'invitación pendiente' : 'invitaciones pendientes'}` : base;
}

/**
 * Why the seats are those of another plan (`limited_by` of the members): D7 says it under the seats.
 * "Cuentan los lugares del plan Creador: tu plan pasa a ese el 12 dic 2026."
 */
export function seatLimitReason(limit: SeatLimit): string {
  return limit.reason === 'pending_plan'
    ? `Cuentan los lugares del plan ${limit.plan_name}: tu plan pasa a ese el ${formatDate(limit.effective_at)}.`
    : `Cuentan los lugares del plan ${limit.plan_name}: estás pasando a ese plan y el pago está en curso.`;
}

/** "2 conectadas · 1 vencida" */
export function networksSummary(accounts: ReadonlyArray<Pick<SocialAccount, 'status'>>): string {
  const connected = accounts.filter((account) => account.status === 'connected').length;
  const expired = accounts.filter((account) => account.status === 'expired').length;
  if (connected === 0 && expired === 0) return 'Ninguna conectada';
  const parts = [];
  if (connected > 0) parts.push(`${connected} ${connected === 1 ? 'conectada' : 'conectadas'}`);
  if (expired > 0) parts.push(`${expired} ${expired === 1 ? 'vencida' : 'vencidas'}`);
  return parts.join(' · ');
}
