"""The IPP client: one printer, the operations Domovoi uses.

Over :mod:`domovoi.printing.http11` (pinned address, ``Content-Length``
bodies, certificate pin checked before sending). Each call is one POST to
the printer's resource path with ``Content-Type: application/ipp``; the
document, when there is one, follows the attributes in the same body,
streamed from a file so a 100-page raster job is never held whole in
memory.

Quirks handled here (plan part 1, P0 2026-10-21):

* ``server-error-busy`` is retried with backoff (3 tries);
* every 0x000x status is success (Brother answers 0x0001 to
  Get-Job-Attributes);
* the ``printer-uri`` we send names the host as written; a printer that
  reports a different host in its own ``printer-uri-supported`` is fine.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from domovoi.printing import http11, ipp
from domovoi.printing.ipp import Attr

log = logging.getLogger(__name__)

PRINTER_ATTRS_STATUS = [
    "printer-state", "printer-state-reasons", "printer-state-message", "printer-is-accepting-jobs",
    "marker-names", "marker-levels", "marker-low-levels", "marker-high-levels", "marker-colors",
    "marker-types", "printer-supply", "printer-supply-description", "queued-job-count",
]


class IppError(Exception):
    """The printer answered, but not with success. ``status`` is the IPP
    status code (0 when the failure was below IPP: HTTP or the codec)."""

    def __init__(self, message: str, status: int = 0, http_status: int = 0) -> None:
        super().__init__(message)
        self.status = status
        self.http_status = http_status


@dataclass
class Printer:
    """How to reach one printer."""

    address: str
    port: int
    path: str
    host: str
    tls: bool = False
    cert_sha256: str | None = None

    @property
    def uri(self) -> str:
        scheme = "ipps" if self.tls else "ipp"
        host = self.host if ":" not in self.host else "[%s]" % self.host
        port = "" if self.port == 631 else ":%d" % self.port
        return "%s://%s%s%s" % (scheme, host, port, self.path)

    def endpoint(self) -> http11.Endpoint:
        return http11.Endpoint(
            address=self.address, port=self.port, tls=self.tls, host=self.host,
            cert_sha256=self.cert_sha256,
        )


class IppClient:
    def __init__(self, printer: Printer, *, user: str = "domovoi", timeout: float = 30.0) -> None:
        self.printer = printer
        self.user = user
        self.timeout = timeout
        self._rid = 0
        self.last_cert_sha256: str | None = None

    async def call(
        self,
        operation: str,
        op_extra: list[Attr] | None = None,
        job_attrs: list[Attr] | None = None,
        *,
        document: list[http11.BodyPart] | None = None,
        retries: int = 3,
    ) -> ipp.Message:
        groups: list[tuple[int, list[Attr]]] = [
            (ipp.OPERATION, ipp.operation_attrs(self.printer.uri, *(op_extra or [])))
        ]
        if job_attrs:
            groups.append((ipp.JOB, job_attrs))
        delay = 1.0
        for attempt in range(retries):
            self._rid += 1
            head = ipp.encode_request(operation, self._rid, groups)
            resp = await http11.request(
                self.printer.endpoint(), "POST", self.printer.path,
                headers={"Content-Type": "application/ipp"},
                body=[head, *(document or [])],
                max_bytes=ipp.MAX_RESPONSE,
                timeout=self.timeout if not document else max(self.timeout, 300.0),
            )
            self.last_cert_sha256 = resp.cert_sha256
            if resp.status != 200:
                raise IppError("the printer answered HTTP %d" % resp.status, http_status=resp.status)
            try:
                msg = ipp.decode(resp.body)
            except ipp.IppDecodeError as e:
                raise IppError("the printer's answer didn't make sense (%s)" % e) from e
            if msg.status == ipp.STATUS_BUSY and attempt + 1 < retries and not document:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            return msg
        return msg

    async def checked(self, *args: Any, **kw: Any) -> ipp.Message:
        msg = await self.call(*args, **kw)
        if not msg.ok:
            raise IppError("%s: %s" % (args[0], msg.status_name), status=msg.status)
        return msg

    # ── operations ──────────────────────────────────────────────────────

    async def get_printer_attributes(self, requested: list[str] | None = None) -> dict[str, list[Any]]:
        msg = await self.checked(
            "Get-Printer-Attributes",
            [Attr.of(ipp.KEYWORD, "requested-attributes", requested or ["all", "media-col-database"])],
        )
        return msg.group(ipp.PRINTER)

    def _job_op_attrs(self, job_name: str | None, document_format: str | None) -> list[Attr]:
        out = [Attr.of(ipp.NAME, "requesting-user-name", self.user)]
        if job_name:
            out.append(Attr.of(ipp.NAME, "job-name", job_name[:200]))
        if document_format:
            out.append(Attr.of(ipp.MIME_TYPE, "document-format", document_format))
        return out

    async def validate_job(
        self, job_attrs: list[Attr], *, document_format: str, job_name: str | None = None
    ) -> ipp.Message:
        """Answers as it comes (not raised): the caller reads the
        unsupported-attributes group to adjust options."""
        return await self.call("Validate-Job", self._job_op_attrs(job_name, document_format), job_attrs)

    async def create_job(self, job_attrs: list[Attr], *, job_name: str) -> int:
        msg = await self.checked("Create-Job", self._job_op_attrs(job_name, None), job_attrs)
        jid = ipp.first(msg.group(ipp.JOB), "job-id")
        if not isinstance(jid, int):
            raise IppError("the printer didn't give the job a number")
        return jid

    async def send_document(self, job_id: int, data: list[http11.BodyPart], *, document_format: str) -> ipp.Message:
        return await self.checked(
            "Send-Document",
            [
                Attr.of(ipp.INTEGER, "job-id", job_id),
                Attr.of(ipp.NAME, "requesting-user-name", self.user),
                Attr.of(ipp.MIME_TYPE, "document-format", document_format),
                Attr.of(ipp.BOOLEAN, "last-document", True),
            ],
            document=data,
            retries=1,
        )

    async def print_job(
        self, job_attrs: list[Attr], data: list[http11.BodyPart], *, document_format: str, job_name: str
    ) -> int:
        msg = await self.checked(
            "Print-Job", self._job_op_attrs(job_name, document_format), job_attrs,
            document=data, retries=1,
        )
        jid = ipp.first(msg.group(ipp.JOB), "job-id")
        if not isinstance(jid, int):
            raise IppError("the printer didn't give the job a number")
        return jid

    async def get_job_attributes(self, job_id: int) -> dict[str, list[Any]]:
        msg = await self.checked(
            "Get-Job-Attributes",
            [
                Attr.of(ipp.INTEGER, "job-id", job_id),
                Attr.of(ipp.KEYWORD, "requested-attributes", [
                    "job-state", "job-state-reasons", "job-state-message",
                    "job-impressions-completed", "job-media-sheets-completed",
                ]),
            ],
        )
        return msg.group(ipp.JOB)

    async def cancel_job(self, job_id: int) -> ipp.Message:
        return await self.call(
            "Cancel-Job",
            [Attr.of(ipp.INTEGER, "job-id", job_id), Attr.of(ipp.NAME, "requesting-user-name", self.user)],
        )

    async def identify(self, actions: list[str] | None = None) -> ipp.Message:
        extra = [Attr.of(ipp.NAME, "requesting-user-name", self.user)]
        if actions:
            extra.append(Attr.of(ipp.KEYWORD, "identify-actions", actions))
        return await self.checked("Identify-Printer", extra)


def file_part(path: Path) -> http11.BodyPart:
    return path
