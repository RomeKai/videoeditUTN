'use client';

import { useState, type FormEvent } from 'react';
import { useRenameProject } from '@/api/hooks';
import { errorMessage } from '@/lib/error-messages';
import { Button, Dialog, Field, Input, useToast } from '@/ui';

export interface RenameProjectDialogProps {
  project: { id: string; title: string };
  onClose: () => void;
}

/** Renombrar un proyecto desde la lista. Se monta solo mientras está abierto (una instancia por proyecto). */
export function RenameProjectDialog({ project, onClose }: RenameProjectDialogProps) {
  const rename = useRenameProject(project.id);
  const toast = useToast();
  const [title, setTitle] = useState(project.title);
  const [touched, setTouched] = useState(false);
  const clean = title.trim();
  const empty = clean.length === 0;
  const fieldError = touched && empty ? 'Ponele un nombre al proyecto.' : rename.isError ? errorMessage(rename.error, 'No pudimos renombrar el proyecto.') : undefined;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (empty) return;
    if (clean === project.title) {
      onClose();
      return;
    }
    rename.mutate(clean, {
      onSuccess: () => {
        toast.success('Proyecto renombrado');
        onClose();
      },
    });
  };

  return (
    <Dialog
      open
      onClose={onClose}
      size="sm"
      title="Renombrar proyecto"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button variant="primary" type="submit" form="rename-project" loading={rename.isPending}>
            Guardar
          </Button>
        </>
      }
    >
      <form id="rename-project" onSubmit={submit} noValidate>
        <Field label="Nombre" error={fieldError}>
          <Input value={title} maxLength={255} onChange={(event) => setTitle(event.target.value)} data-autofocus autoComplete="off" />
        </Field>
      </form>
    </Dialog>
  );
}
