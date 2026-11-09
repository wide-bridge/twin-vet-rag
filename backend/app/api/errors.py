"""Uniform error body: {"error": {"code", "message"}}. Messages are Korean and safe to show users;
internal details (stack traces, SQL, keys) never leave the server."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

MESSAGES = {
    "bad_request": "요청을 처리할 수 없어요.",
    "empty_question": "질문을 입력해 주세요.",
    "question_too_long": "질문이 너무 길어요.",
    "invalid_input": "입력값을 확인해 주세요.",
    "session_expired": "상담 시간이 만료되어 이전 대화는 볼 수 없어요.",
    "forbidden_origin": "허용되지 않은 요청이에요.",
    "not_found": "찾을 수 없어요.",
    "busy": "이전 질문의 답변을 준비하고 있어요. 답변이 끝난 뒤 다시 보내 주세요.",
    "request_conflict": "같은 요청 번호로 다른 질문을 보낼 수 없어요.",
    "rate_limited": "질문이 너무 많아요. 잠시 후 다시 시도해 주세요.",
    "internal_error": "일시적인 오류가 발생했어요. 잠시 후 다시 시도해 주세요.",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str | None = None, extra: dict | None = None):
        self.status, self.code = status, code
        self.message = message or MESSAGES.get(code, MESSAGES["bad_request"])
        self.extra = extra or {}


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError):
        return JSONResponse({"error": {"code": e.code, "message": e.message, **e.extra}}, status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError):
        fields = sorted({".".join(str(p) for p in err["loc"] if p != "body") for err in e.errors()})
        return JSONResponse({"error": {"code": "invalid_input", "message": MESSAGES["invalid_input"], "fields": fields}},
                            status_code=422)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, e: Exception):  # logged by the server; body stays generic
        return JSONResponse({"error": {"code": "internal_error", "message": MESSAGES["internal_error"]}}, status_code=500)
