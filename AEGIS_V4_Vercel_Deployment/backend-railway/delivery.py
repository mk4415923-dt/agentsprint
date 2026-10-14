"""Gmail delivery error classification: ambiguous transport failures MUST NOT be auto-retried."""
import urllib.error
import re


def classify_delivery_exception(exc):
    # HTTP 4xx without a Gmail receipt is a definitively rejected API request;
    # some 5xx & transport failures may have been accepted before connection broke.
    if isinstance(exc, urllib.error.HTTPError) and 400 <= exc.code < 500:
        return 'send_failed', 'Gmail rejected the request; verify permissions, recipient, or quota.'
    msg=str(exc).lower()
    if re.search(r'http 4(?:00|01|03|04|09|22|29)',msg):
        return 'send_failed', 'Gmail definitively rejected the request (HTTP 4xx). Verify authorization or input.'
    if any(s in msg for s in ('permission is missing','delivery is disabled','not connected',
                             'recipient email address is missing','invalid recipient','not authorized')):
        return 'send_failed', 'Gmail request was blocked before submission.'
    return 'send_uncertain', ('Gmail request outcome is unknown due to a transport/server failure. '
                              'Check Gmail Sent folder before attempting another send.')
