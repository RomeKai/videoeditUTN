"""
The emails of the notifications (map 1.11, gap 21): what each one says.

One email per notification of the bell whose kind is in ``EMAIL_KINDS``, for a
user who has that kind on in Perfil › Notificaciones (``NotificationPreference``,
a field with the kind's name). A payment has no email (map 1.11) and no
preference.

No email carries anything a user typed (the rule of the account emails, see
``apps.users.email_verification``): no project title, no workspace name, no
person's name. The bell's title has the project's title (the clip's, for a
post that failed), so it never goes in an email. What does go:

- the notification's ``body``, which the events write without user text (how
  many clips, what was returned, with the same money texts as the bell);
- for a ``render_finished`` with the reason ``Notification.CLIP_RENDER_FAILED``
  (one clip's render that was returned whole), its own subject and text: it
  does not say a render ended;
- for ``post_failed``, its body is the API's `error_message` of the post
  (what happened, by its code, with the network's name); its ``reason`` (the
  code) picks the subject: "it may be out" never reads as "it failed";
- for ``low_balance``, also its title (the balance: "Te quedan 300 tokens");
- links built from ``FRONTEND_URL`` and ids. The paths are the web app's for
  each kind (``notificationHref`` in front/src/shell/nav.ts), with the
  notification's workspace in ``?espacio=<id>``: the web app moves to that
  workspace before it shows the page (RequireSession). Without it, a member
  who has another workspace active would open a project that is not there,
  or buy tokens for the wrong workspace. A failed post leads to itself in
  Publicaciones (``?detalle=<id>``, the parameter the screen opens a post's
  detail with).

Each email has a plain text part and a simple HTML part with the same words;
everything that goes into the HTML is escaped.
"""
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import format_html, format_html_join

from .models import Notification

Kind = Notification.Kind

# The web app's screen with the switches (I2).
PREFERENCES_PATH = '/perfil/notificaciones'
BUY_TOKENS_PATH = '/uso/comprar'
# Publicaciones and the parameter its screen opens one post's detail with
# (front/src/features/publicar/PublicacionesScreen.tsx; notificationHref builds the same link).
POSTS_PATH = '/publicaciones'
POST_PARAM = 'detalle'

# kind → the label of its switch in I2. The preference field has the kind's name.
EMAIL_KINDS = {
    Kind.PROJECT_READY: 'El proyecto está listo para revisar',
    Kind.RENDER_FINISHED: 'El render terminó',
    Kind.PROJECT_FAILED: 'El proyecto falló',
    Kind.POST_FAILED: 'Una publicación falló',
    Kind.LOW_BALANCE: 'Te queda poco saldo',
}


# The query parameter RequireSession reads (front/src/shell/RequireSession.tsx).
WORKSPACE_PARAM = 'espacio'


def frontend_url(path, workspace_id=None):
    """``path`` in the web app; with ``workspace_id``, in that workspace."""
    query = f"{'&' if '?' in path else '?'}{WORKSPACE_PARAM}={workspace_id}" if workspace_id else ''
    return f"{settings.FRONTEND_URL.rstrip('/')}{path}{query}"


def wants_email(user, kind):
    """The user's switch of ``kind``. Without a preferences row, every switch is on (the model's defaults)."""
    if kind not in EMAIL_KINDS:
        return False
    preferences = getattr(user, 'notification_preferences', None)
    return True if preferences is None else bool(getattr(preferences, kind))


def _project_path(notification, suffix=''):
    return f'/proyectos/{notification.project_id}{suffix}' if notification.project_id else '/proyectos'


def _content(notification):
    """(subject, paragraphs, button label, path) of the notification's email."""
    kind, detail = notification.kind, notification.body
    if kind == Kind.PROJECT_READY:
        return (
            'Tu proyecto está listo para revisar',
            [
                f'Terminamos de analizar un video de tu espacio de trabajo en OneCreator. {detail}'.strip(),
                'Todavía no se renderizó nada. Elegí los clips que te sirven, ajustá el recorte si hace falta '
                'y mirá el costo antes de confirmar.',
            ],
            'Revisar clips',
            _project_path(notification, '/revision'),
        )
    if kind == Kind.RENDER_FINISHED and notification.reason == Notification.CLIP_RENDER_FAILED:
        # One clip's render that gave nothing: no render ended and nothing new is ready.
        return (
            'No pudimos renderizar un clip de tu proyecto',
            [
                f'No pudimos renderizar un clip de un proyecto de tu espacio de trabajo en OneCreator. {detail}'.strip(),
                'Abrí el proyecto para ver el clip y volver a intentarlo cuando quieras.',
            ],
            'Ver clips',
            _project_path(notification, '/clips'),
        )
    if kind == Kind.RENDER_FINISHED:
        return (
            'Terminó el render de tu proyecto',
            [
                f'Terminó el render de un proyecto de tu espacio de trabajo en OneCreator. {detail}'.strip(),
                'Abrí el proyecto para ver, descargar o publicar lo que quedó listo.',
            ],
            'Ver clips',
            _project_path(notification, '/clips'),
        )
    if kind == Kind.PROJECT_FAILED:
        return (
            'No pudimos terminar tu proyecto',
            [
                f'No pudimos terminar un proyecto de tu espacio de trabajo en OneCreator. {detail}'.strip(),
                'Abrí el proyecto para ver qué pasó y qué podés hacer.',
            ],
            'Ver el proyecto',
            _project_path(notification),
        )
    if kind == Kind.POST_FAILED:
        from .services import NETWORK_DISCONNECTED, POST_MAYBE_OUT, SEVERAL_POSTS_FAILED

        path = f'{POSTS_PATH}?{POST_PARAM}={notification.post_id}' if notification.post_id else POSTS_PATH
        if notification.reason in (NETWORK_DISCONNECTED, SEVERAL_POSTS_FAILED):
            # The bell's title is ours here (a network and a count, or a fixed sentence): no user text.
            return (
                'Hay publicaciones que no van a salir'
                if notification.reason == NETWORK_DISCONNECTED else 'Fallaron varias publicaciones',
                [
                    f'{notification.title} en tu espacio de trabajo de OneCreator. {detail}'.strip(),
                    'Hasta que alguien las reintente, quedan en error y no se publican.',
                ],
                'Ver publicaciones',
                path,
            )
        if notification.reason in POST_MAYBE_OUT:
            return (
                'No sabemos si una publicación salió',
                [
                    f'No pudimos confirmar una publicación de tu espacio de trabajo en OneCreator. {detail}'.strip(),
                    'Puede que ya esté publicada: mirá la cuenta de la red antes de reintentarla, para que no salga dos veces.',
                ],
                'Ver la publicación',
                path,
            )
        return (
            'Una publicación no salió',
            [
                f'No pudimos publicar un clip de tu espacio de trabajo en OneCreator. {detail}'.strip(),
                'No se va a publicar sola: abrila para ver qué pasó y reintentarla.',
            ],
            'Ver la publicación',
            path,
        )
    if kind == Kind.LOW_BALANCE:
        return (
            'Te queda poco saldo en OneCreator',
            [
                f'{notification.title} en tu espacio de trabajo de OneCreator. {detail}'.strip(),
                'Si un proyecto cuesta más de lo que tenés, te avisamos antes de empezar y no se cobra nada. '
                'Los tokens que comprás no vencen y se suman al saldo del espacio de trabajo.',
            ],
            'Comprar tokens',
            BUY_TOKENS_PATH,
        )
    raise ValueError(kind)


def build(notification):
    """``(subject, text, html)`` of the email of ``notification`` (a kind of ``EMAIL_KINDS``)."""
    subject, paragraphs, label, path = _content(notification)
    link = frontend_url(path, notification.workspace_id)
    preferences = frontend_url(PREFERENCES_PATH)
    why = f'Recibís este email porque tenés activado el aviso «{EMAIL_KINDS[notification.kind]}».'
    text = (
        'Hola:\n\n'
        + '\n\n'.join(paragraphs)
        + f'\n\n{label}:\n{link}\n\n'
        + f'{why} Podés apagarlo en Perfil › Notificaciones:\n{preferences}\n'
    )
    html = format_html(
        '<!doctype html>\n<html lang="es"><body style="font-family:sans-serif;color:#111;line-height:1.5">'
        '<p>Hola:</p>{}'
        '<p><a href="{}" style="display:inline-block;padding:10px 16px;background:#111;color:#fff;'
        'text-decoration:none;border-radius:6px">{}</a></p>'
        '<p style="font-size:13px;color:#555">Si el botón no anda, copiá este link: {}</p>'
        '<p style="font-size:13px;color:#555">{} Podés apagarlo en <a href="{}">Perfil › Notificaciones</a>.</p>'
        '</body></html>',
        format_html_join('', '<p>{}</p>', ((paragraph,) for paragraph in paragraphs)),
        link, label, link, why, preferences,
    )
    return subject, text, str(html)


def send(notification):
    """Sends the email of ``notification`` to its user (Django's email backend)."""
    subject, text, html = build(notification)
    message = EmailMultiAlternatives(subject=subject, body=text, from_email=None, to=[notification.user.email])
    message.attach_alternative(html, 'text/html')
    message.send()
