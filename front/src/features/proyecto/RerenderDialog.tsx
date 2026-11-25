'use client';

import { useEffect, useState } from 'react';
import type { EditingStyle, Project, SubtitleSize } from '@/api/contracts/projects';
import { usePricing, useRerenderProject, useWallet } from '@/api/hooks';
import { ConfirmCostDialog, useBillingDialogs } from '@/dialogs';
import { errorMessage } from '@/lib/error-messages';
import { estimateCost } from '@/lib/pricing';
import { LABELS } from '@/lib/project-status';
import { usePlan } from '@/shell/session';
import { Button, Dialog, Field, Select, Switch, useToast } from '@/ui';
import styles from './ResultScreen.module.css';

const STYLES: EditingStyle[] = ['dynamic', 'minimalist', 'vlog', 'hormozi'];
const SIZES: SubtitleSize[] = ['small', 'medium', 'large'];

export interface RerenderDialogProps {
  open: boolean;
  onClose: () => void;
  project: Project;
  /** Seconds that get rendered again (the sum of the clips). */
  durationSeconds: number;
}

/** Cost of rendering `durationSeconds` again at the project's resolution, with the plan's discount. Null until prices load. */
export function useRerenderEstimate(project: Pick<Project, 'metadata'>, durationSeconds: number) {
  const pricing = usePricing();
  const { plan } = usePlan();
  const resolution = project.metadata.render_resolution ?? '720p';
  const estimate = pricing.data ? estimateCost({ durationSeconds: Math.ceil(durationSeconds), resolution }, pricing.data, plan) : null;
  return { estimate, resolution, plan, pricing: pricing.data ?? null };
}

/**
 * "Volver a renderizar con otro estilo": first the style, then the cost
 * (dialog D1). A 402 opens the insufficient balance dialog.
 */
export function RerenderDialog({ open, onClose, project, durationSeconds }: RerenderDialogProps) {
  const toast = useToast();
  const billing = useBillingDialogs();
  const wallet = useWallet({ enabled: open });
  const rerender = useRerenderProject(project.id);
  const { estimate, resolution, plan, pricing } = useRerenderEstimate(project, durationSeconds);
  const [step, setStep] = useState<'style' | 'cost'>('style');
  const [style, setStyle] = useState<EditingStyle>(project.editing_style);
  const [subtitles, setSubtitles] = useState(project.add_subtitles);
  const [size, setSize] = useState<SubtitleSize>(project.subtitle_size);

  useEffect(() => {
    if (!open) return;
    setStep('style');
    setStyle(project.editing_style);
    setSubtitles(project.add_subtitles);
    setSize(project.subtitle_size);
  }, [open, project.editing_style, project.add_subtitles, project.subtitle_size]);

  const close = () => {
    rerender.reset();
    onClose();
  };
  const changed = style !== project.editing_style || subtitles !== project.add_subtitles || (subtitles && size !== project.subtitle_size);

  if (step === 'cost' && estimate) {
    return (
      <ConfirmCostDialog
        open={open}
        onClose={close}
        title="Volver a renderizar"
        subtitle={`${project.title} · estilo ${LABELS.editing_style[style]}`}
        durationSeconds={Math.ceil(durationSeconds)}
        resolution={resolution}
        estimate={estimate}
        planName={plan?.name}
        balance={wallet.data?.available_balance ?? 0}
        baseRate={pricing?.base_cost_per_second_720p}
        confirmLabel="Renderizar"
        loading={rerender.isPending}
        error={rerender.isError ? errorMessage(rerender.error, 'No pudimos empezar el render.') : undefined}
        onConfirm={() =>
          rerender.mutate(
            { editing_style: style, add_subtitles: subtitles, subtitle_size: size },
            {
              onSuccess: () => {
                toast.success('Render en marcha. Te avisamos cuando termine.');
                onClose();
              },
              onError: (error) => {
                if (billing.handleError(error, { concept: 'volver a renderizar' })) close();
              },
            },
          )
        }
      />
    );
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Volver a renderizar con otro estilo"
      description="Se genera el video de nuevo con lo que elijas acá. El formato y el recorte no cambian."
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            Cancelar
          </Button>
          <Button
            variant="primary"
            disabled={!estimate || !wallet.data}
            data-autofocus
            onClick={() => {
              if (!estimate || !wallet.data) return;
              // The estimate already says it does not fit: straight to "No te alcanza el saldo" (D2).
              if (estimate.total > wallet.data.available_balance) {
                billing.showInsufficientBalance({ required: estimate.total, concept: 'Costo del render' });
                close();
                return;
              }
              setStep('cost');
            }}
          >
            Ver el costo
          </Button>
        </>
      }
    >
      <div className={styles.form}>
        <Field label="Estilo">
          <Select value={style} onChange={(event) => setStyle(event.target.value as EditingStyle)} options={STYLES.map((value) => ({ value, label: LABELS.editing_style[value] }))} />
        </Field>
        <Switch checked={subtitles} onChange={setSubtitles} label="Subtítulos" description="Se escriben con lo que se dice en el video." />
        {subtitles ? (
          <Field label="Tamaño de los subtítulos">
            <Select value={size} onChange={(event) => setSize(event.target.value as SubtitleSize)} options={SIZES.map((value) => ({ value, label: LABELS.subtitle_size[value] }))} />
          </Field>
        ) : null}
        {!changed ? <p className="t-small muted">Todavía no cambiaste nada: saldría el mismo video.</p> : null}
      </div>
    </Dialog>
  );
}
