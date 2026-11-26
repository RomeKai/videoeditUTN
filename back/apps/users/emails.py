"""
One spelling per email address (documents/ia/07, "Cuentas y emails").

The domain of an email can be written in many ways that deliver to the same
mailbox: in upper case, with full-width letters ("ｔｅｓｔ.com"), in Unicode
("mañana.com") or in its ASCII form ("xn--maana-pta.com"). Each one would be
its own account under the unique constraint of ``User.email``. Every email
that enters (``User.save``, the login lookup, ``PATCH /me``, the admin) goes
through ``normalize_email``: the domain is stored in the ASCII form mail
servers use. The local part is left as typed: only its mail server knows
which spellings of it are the same mailbox.
"""
import re
import unicodedata

INVALID_EMAIL_DOMAIN = 'El dominio del email no es válido. Revisá que esté bien escrito.'

# What an encoded host name may have: letters, digits and hyphens in each label.
_LABEL = re.compile(r'(?!-)[a-z0-9-]{1,63}(?<!-)')


class InvalidEmailDomain(ValueError):
    """The domain of the email cannot be written as an ASCII host name."""

    def __init__(self):
        super().__init__(INVALID_EMAIL_DOMAIN)


def normalize_domain(domain):
    """
    Unicode NFKC, then IDNA to ASCII (punycode), then lower case.
    Raises InvalidEmailDomain when the result is not a host name.
    """
    if domain.startswith('[') and domain.endswith(']'):
        # An address literal ("[127.0.0.1]"): nothing to encode.
        return domain.lower()
    try:
        encoded = unicodedata.normalize('NFKC', domain).encode('idna').decode('ascii').lower()
    except UnicodeError:
        raise InvalidEmailDomain() from None
    # NFKC can produce characters no host name has ("℀" becomes "a/c"), and the
    # codec lets ASCII through unchecked.
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
    return f'{local}@{normalize_domain(domain)}'


def accounts_to_review(users):
    """
    The accounts whose stored email is not in its normalized spelling, as
    lists of ids (`users` is a queryset of User):

    - ``collisions``: another account already has the normalized email. Both
      receive their mail in the same mailbox; the login with that email signs
      into the other one.
    - ``invalid``: the domain cannot be encoded.
    - ``pending``: nothing in the way, saving the account normalizes it.
    """
    found = {'collisions': [], 'invalid': [], 'pending': []}
    for pk, email in users.order_by('pk').values_list('pk', 'email').iterator():
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
