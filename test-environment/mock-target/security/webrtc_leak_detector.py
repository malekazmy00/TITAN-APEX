"""WebRTC leak detection -- docs/PHASE_2_BACKLOG.md item 5 (WebRTC Leak
Prevention, a real confirmed gap: neither real-browser antibot
provider disabled WebRTC by default, so a page's own JS could
enumerate real local/network IP addresses via ``RTCPeerConnection``
ICE candidates regardless of every other anti-fingerprinting layer
this project already has -- see ``src/core/interfaces/antibot_provider.py``'s
own ``block_webrtc`` parameter docstring for the fix on the crawler
side; this module is the mock-target side that actually *detects*
whether a leak happened).

**Deterministic, not statistical** -- unlike the mouse-movement/
keystroke-timing gaps this same backlog documents (those are patterns
judged over many samples), a WebRTC leak either happened on a given
page load or it didn't: a real, non-loopback, non-mDNS-obfuscated IP
address either shows up in the ICE candidates a page's own JS
collected, or it doesn't.

**Real STUN/TURN infrastructure is deliberately never involved** --
test-environment/README.md's own network-isolation rule means this
page can't reach a public STUN server anyway, so every candidate this
module ever sees is a ``host`` candidate (from ``RTCPeerConnection``
enumerating the browser's own local network interfaces, no external
server needed at all -- ``createDataChannel`` + ``createOffer`` is
enough to trigger ICE gathering). That is exactly the candidate class
a real leak would show up in.

**mDNS obfuscation is real and expected, not a gap** -- modern Firefox
and Chromium have both defaulted to masking host candidates' real
local IP behind a randomly-generated ``.local`` hostname for years
(``media.peerconnection.ice.obfuscate_host_addresses.enabled`` in
Firefox) specifically to prevent this exact kind of local-network-
topology leak. This module treats an ``.local`` candidate as
``mDNS`` -- safe, not a leak -- and only flags a candidate carrying an
actual, resolvable IP address as ``LEAKED``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

# SDP candidate-attribute line shape (RFC 8839): "candidate:<foundation>
# <component> <transport> <priority> <address> <port> typ <type> ...".
# Group 1 is the address field -- an IPv4/IPv6 literal or an mDNS
# ".local" hostname, per the same RFC.
_CANDIDATE_ADDRESS_PATTERN = re.compile(
    r"^candidate:\S+ \d+ \S+ \d+ (\S+) \d+ typ \S+"
)

_LOOPBACK_ADDRESSES = frozenset({"127.0.0.1", "::1", "0.0.0.0", "::"})  # noqa: S104


class CandidateClass:
    """The classification :func:`classify_candidate_address` returns.
    A plain string enum (not ``enum.Enum``) -- these are logged
    verbatim into JSON, and a plain string round-trips through
    ``json.dumps``/``caplog`` without an extra ``.value`` at every call
    site, the same style ``TrustScoreTier`` deliberately did *not* use
    (that one needed real ``Enum`` identity for tier-ordering
    comparisons; this one never does).
    """

    LOOPBACK = "loopback"
    MDNS = "mdns"
    LEAKED = "leaked"
    UNPARSEABLE = "unparseable"


def extract_candidate_address(candidate: str) -> str | None:
    """Pull the address field out of one raw SDP candidate line.

    Returns ``None`` if ``candidate`` doesn't match the expected SDP
    candidate-attribute shape at all (a malformed/empty string a
    client might send) -- never raises for bad input, since this is
    parsing untrusted, client-submitted data.
    """
    match = _CANDIDATE_ADDRESS_PATTERN.match(candidate.strip())
    return match.group(1) if match else None


def classify_candidate_address(address: str) -> str:
    """Classify one already-extracted candidate address.

    - :data:`CandidateClass.MDNS`: ends with ``.local`` -- the browser's
      own obfuscation is active, the real local IP was never exposed.
    - :data:`CandidateClass.LOOPBACK`: the loopback address itself --
      expected when no real network interface got enumerated (e.g.
      WebRTC blocked entirely, so gathering had nothing else to find).
    - :data:`CandidateClass.LEAKED`: anything else -- a real, resolvable
      IP address genuinely present in the candidate.
    """
    if address.endswith(".local"):
        return CandidateClass.MDNS
    if address in _LOOPBACK_ADDRESSES:
        return CandidateClass.LOOPBACK
    return CandidateClass.LEAKED


@dataclass
class WebrtcLeakResult:
    webrtc_available: bool
    leak_detected: bool
    leaked_addresses: list[str] = field(default_factory=list)
    candidate_classes: list[str] = field(default_factory=list)


def score_webrtc_report(webrtc_available: bool, candidates: list[str]) -> WebrtcLeakResult:
    """Pure function -- no I/O, independently unit-testable.

    ``webrtc_available=False`` (the browser reported ``RTCPeerConnection``
    itself is unavailable -- e.g. Camoufox's own ``block_webrtc`` sets
    Firefox's ``media.peerconnection.enabled`` pref to ``False``, which
    removes the API from JS entirely) is the strongest possible
    "not leaking" signal -- ``candidates`` is expected empty in that
    case (nothing could have been gathered at all), and this always
    returns ``leak_detected=False`` regardless of what ``candidates``
    contains (a caller passing stale/bogus candidates alongside
    ``webrtc_available=False`` is a malformed report, not evidence of
    a real leak the browser's own API already confirmed doesn't exist).

    Raises:
        TypeError: if ``candidates`` isn't a list -- a malformed report
            can't be meaningfully scored.
    """
    if not isinstance(candidates, list):
        raise TypeError(f"candidates must be a list, got {type(candidates).__name__}")

    if not webrtc_available:
        return WebrtcLeakResult(webrtc_available=False, leak_detected=False)

    classes: list[str] = []
    leaked: list[str] = []
    for candidate in candidates:
        address = extract_candidate_address(str(candidate))
        if address is None:
            classes.append(CandidateClass.UNPARSEABLE)
            continue
        candidate_class = classify_candidate_address(address)
        classes.append(candidate_class)
        if candidate_class == CandidateClass.LEAKED:
            leaked.append(address)

    return WebrtcLeakResult(
        webrtc_available=True,
        leak_detected=bool(leaked),
        leaked_addresses=leaked,
        candidate_classes=classes,
    )


def log_webrtc_leak_report(
    logger: logging.Logger, webrtc_available: bool, candidates: list[str]
) -> WebrtcLeakResult:
    """Logs one WebRTC leak check -- WARNING if a real leak was
    detected, INFO otherwise (same "WARNING only when it actually
    matters" split ``security/botd_integration.py``'s own
    ``log_botd_report`` already uses, unlike
    ``fpscanner_integration.py``'s deliberately-always-INFO choice --
    the difference is this check is deterministic/binary, not a
    monitoring-mode score with no enforcement threshold decided yet).

    Raises:
        TypeError: propagated from :func:`score_webrtc_report`.
    """
    result = score_webrtc_report(webrtc_available, candidates)
    payload: dict[str, Any] = {
        "webrtc_available": result.webrtc_available,
        "leak_detected": result.leak_detected,
        "leaked_addresses": result.leaked_addresses,
        "candidate_classes": result.candidate_classes,
    }
    level = logging.WARNING if result.leak_detected else logging.INFO
    logger.log(level, "webrtc_leak.checked", extra=payload)
    return result
