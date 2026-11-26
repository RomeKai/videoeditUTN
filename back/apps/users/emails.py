"""
One spelling per email address (documents/ia/07, "Cuentas y emails").

The domain of an email can be written in many ways that deliver to the same
mailbox: in upper case, with full-width letters ("ｔｅｓｔ.com"), in Unicode
("mañana.com") or in its ASCII form ("xn--maana-pta.com"). Each one would be
its own account under the unique constraint of ``User.email``. Every email
that enters (``User.save``, the login lookup, ``PATCH /me``, the admin) goes
through ``normalize_email``: the domain is stored in the ASCII form mail
servers use, encoded with IDNA 2008 (UTS 46, non-transitional: "straße.de"
stays its own domain, "xn--strae-oqa.de", and is never folded into
"strasse.de", which belongs to someone else). The local part is left as typed: only its mail server knows
which spellings of it are the same mailbox.
"""
import re
import unicodedata

import idna

INVALID_EMAIL_DOMAIN = 'El dominio del email no es válido. Revisá que esté bien escrito.'
EMAIL_TOO_LONG = 'El email es demasiado largo. Probá con una dirección más corta.'

# The column (EmailField) and a host name. A Unicode domain grows when it is encoded.
MAX_EMAIL_LENGTH = 254
MAX_DOMAIN_LENGTH = 253

# What an encoded host name may have: letters, digits and hyphens in each label.
_LABEL = re.compile(r'(?!-)[a-z0-9-]{1,63}(?<!-)')


class InvalidEmailDomain(ValueError):
    """
    The domain of the email cannot be written as an ASCII host name, or the
    email does not fit once it is. Its message is one of the sentences above:
    it can be shown to the user.
    """

    def __init__(self, message=INVALID_EMAIL_DOMAIN):
        super().__init__(message)
        self.message = message


def normalize_domain(domain):
    """
    Unicode NFKC, then IDNA to ASCII (punycode), then lower case.
    Raises InvalidEmailDomain when the result is not a host name.
    """
    if domain.startswith('[') and domain.endswith(']'):
        # An address literal ("[127.0.0.1]"): nothing to encode.
        return domain.lower()
    if len(domain) > MAX_DOMAIN_LENGTH:
        # Not worth normalizing (and slow to): it only grows.
        raise InvalidEmailDomain(EMAIL_TOO_LONG)
    try:
        encoded = idna.encode(unicodedata.normalize('NFKC', domain), uts46=True, transitional=False)
    except (idna.IDNAError, UnicodeError) as error:
        # idna refuses a domain over 253 characters itself ("Domain too long").
        too_long = 'domain too long' in str(error).lower()
        raise InvalidEmailDomain(EMAIL_TOO_LONG if too_long else INVALID_EMAIL_DOMAIN) from None
    encoded = encoded.decode('ascii').lower()
    if len(encoded) > MAX_DOMAIN_LENGTH:
        raise InvalidEmailDomain(EMAIL_TOO_LONG)
    if not all(_LABEL.fullmatch(label) for label in encoded.split('.')):
        raise InvalidEmailDomain()
    return encoded


def normalize_email(email):
    """
    The email with its domain normalized. A value without a local part and a
    domain is returned as it came (stripped), like Django's normalize_email.
    """
    email = (email or '').strip()
    local, at, domain = email.rpartition('@')
    if not at or not local or not domain:
        return email
    normalized = f'{local}@{normalize_domain(domain)}'
    if len(normalized) > MAX_EMAIL_LENGTH:
        raise InvalidEmailDomain(EMAIL_TOO_LONG)
    return normalized


def accounts_to_review(users):
    """
    The accounts whose stored emails are not in their normalized spelling, as
    lists of ids (`users` is a queryset of User):

    - ``collisions``: another account already has the normalized email. Both
      receive their mail in the same mailbox; the login with that email signs
      into the other one.
    - ``invalid``: the domain of the email cannot be encoded (or the email
      does not fit once it is).
    - ``pending``: nothing in the way, saving the account normalizes it.
    - ``pending_email``: the email waiting for its link (``pending_email``) is
      not normalized or cannot be: its link will not work. An account can be
      here and in one of the others.
    """
    found = {'collisions': [], 'invalid': [], 'pending': [], 'pending_email': []}
    rows = users.order_by('date_joined', 'pk').values_list('pk', 'email', 'pending_email')
    for pk, email, pending_email in rows.iterator():
        if pending_email:
            try:
                if normalize_email(pending_email) != pending_email:
                    found['pending_email'].append(pk)
            except InvalidEmailDomain:
                found['pending_email'].append(pk)
        try:
            target = normalize_email(email)
        except InvalidEmailDomain:
            found['invalid'].append(pk)
            continue
        if target == email:
            continue
        taken = users.filter(email__iexact=target).exclude(pk=pk).exists()
        found['collisions' if taken else 'pending'].append(pk)
    return found
