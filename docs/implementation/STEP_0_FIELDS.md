# Поля контрактов шага 0

Сгенерировано из Pydantic-моделей. Не редактировать вручную.
Все выходные поля обязательны, включая nullable; у конфигурации есть defaults.
`null` означает неприменимость или отсутствие наблюдения, а не успех проверки.
JSON Schema описывает форму; межполевые инварианты дополнительно проверяет Python.

## Step0Config / Step0Config

| Поле | Обязательно | null | Тип / ограничения |
|---|---|---|---|
| schema_version | нет | нет | `{"default": "1.1.0", "enum": ["1.0.0", "1.1.0"], "type": "string"}` |
| config_version | нет | нет | `{"default": "step0-config-1.1.0", "enum": ["step0-config-1.0.0", "step0-config-1.1.0"], "type": "string"}` |
| allow_http_fallback | нет | нет | `{"default": true, "type": "boolean"}` |
| allowed_hosts | нет | нет | `{"default": [], "items": {"type": "string"}, "type": "array"}` |
| allowed_extra_ports | нет | нет | `{"default": [], "items": {"type": "integer"}, "type": "array"}` |
| path_scope | нет | нет | `{"default": [], "items": {"type": "string"}, "type": "array"}` |
| step0_deadline_seconds | нет | нет | `{"default": 60.0, "exclusiveMinimum": 0, "type": "number"}` |
| total_deadline_seconds | нет | нет | `{"default": 300.0, "exclusiveMinimum": 0, "type": "number"}` |
| step0_max_attempts | нет | нет | `{"default": 15, "minimum": 1, "type": "integer"}` |
| total_max_attempts | нет | нет | `{"default": 150, "minimum": 1, "type": "integer"}` |
| connect_timeout_seconds | нет | нет | `{"default": 5.0, "exclusiveMinimum": 0, "type": "number"}` |
| read_timeout_seconds | нет | нет | `{"default": 15.0, "exclusiveMinimum": 0, "type": "number"}` |
| write_timeout_seconds | нет | нет | `{"default": 10.0, "exclusiveMinimum": 0, "type": "number"}` |
| pool_timeout_seconds | нет | нет | `{"default": 5.0, "exclusiveMinimum": 0, "type": "number"}` |
| max_redirect_hops | нет | нет | `{"default": 5, "minimum": 0, "type": "integer"}` |
| max_retries | нет | нет | `{"default": 2, "minimum": 0, "type": "integer"}` |
| retry_delays_seconds | нет | нет | `{"default": [1.0, 2.0], "items": {"exclusiveMinimum": 0, "type": "number"}, "type": "array"}` |
| retry_jitter_seconds | нет | нет | `{"default": 0.2, "minimum": 0, "type": "number"}` |
| max_encoded_bytes | нет | нет | `{"default": 5242880, "minimum": 1, "type": "integer"}` |
| max_decoded_bytes | нет | нет | `{"default": 5242880, "minimum": 1, "type": "integer"}` |
| sniff_bytes | нет | нет | `{"default": 4096, "maximum": 4096, "minimum": 1, "type": "integer"}` |
| min_host_interval_seconds | нет | нет | `{"default": 1.0, "exclusiveMinimum": 0, "type": "number"}` |
| user_agent | нет | нет | `{"default": "GEOAudit/0.1", "minLength": 1, "type": "string"}` |
| accept_language | нет | нет | `{"default": "ru,en;q=0.8", "minLength": 1, "type": "string"}` |
| source_timezone | нет | нет | `{"default": "Europe/Moscow", "type": "string"}` |
| tls_verify | нет | нет | `{"const": true, "default": true, "type": "boolean"}` |
| trust_env | нет | нет | `{"const": false, "default": false, "type": "boolean"}` |
| proxy_mode | нет | нет | `{"const": "disabled", "default": "disabled", "type": "string"}` |
| cookie_mode | нет | нет | `{"const": "disabled", "default": "disabled", "type": "string"}` |
| dns_policy | нет | нет | `{"const": "public_only_pinned", "default": "public_only_pinned", "type": "string"}` |

## SiteValidationResult / SiteValidationResult

| Поле | Обязательно | null | Тип / ограничения |
|---|---|---|---|
| schema_version | да | нет | `{"enum": ["1.0.0", "1.1.0"], "type": "string"}` |
| algorithm_version | да | нет | `{"enum": ["step0-1.0", "step0-1.1"], "type": "string"}` |
| config_version | да | нет | `{"enum": ["step0-config-1.0.0", "step0-config-1.1.0"], "type": "string"}` |
| config | да | нет | `{"$ref": "#/$defs/Step0Config"}` |
| config_sha256 | да | нет | `{"pattern": "^[a-f0-9]{64}$", "type": "string"}` |
| run_id | да | нет | `{"pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$", "type": "string"}` |
| parent_run_id | да | да | `{"anyOf": [{"pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$", "type": "string"}, {"type": "null"}]}` |
| started_at | да | нет | `{"format": "date-time", "type": "string"}` |
| finished_at | да | нет | `{"format": "date-time", "type": "string"}` |
| source_timezone | да | нет | `{"type": "string"}` |
| input_url | да | нет | `{"type": "string"}` |
| normalized_url | да | да | `{"anyOf": [{"format": "uri", "pattern": "^https?://", "type": "string"}, {"type": "null"}]}` |
| input_scheme_explicit | да | да | `{"anyOf": [{"type": "boolean"}, {"type": "null"}]}` |
| input_host_unicode | да | да | `{"anyOf": [{"type": "string"}, {"type": "null"}]}` |
| effective_url | да | да | `{"anyOf": [{"format": "uri", "pattern": "^https?://", "type": "string"}, {"type": "null"}]}` |
| origin | да | да | `{"anyOf": [{"type": "string"}, {"type": "null"}]}` |
| entry_path | да | да | `{"anyOf": [{"type": "string"}, {"type": "null"}]}` |
| allowed_hosts | да | нет | `{"items": {"type": "string"}, "type": "array"}` |
| path_scope | да | нет | `{"items": {"type": "string"}, "type": "array"}` |
| status | да | нет | `{"enum": ["ok", "partial", "needs_input", "failed"], "type": "string"}` |
| can_enter_step1 | да | нет | `{"type": "boolean"}` |
| content_state | да | нет | `{"enum": ["not_received", "html", "js_candidate", "access_barrier", "suspicious_html", "empty", "non_html", "http_denied", "http_missing", "http_unavailable", "http_other", "incomplete", "too_large"], "type": "string"}` |
| diagnostic_allowed | да | нет | `{"type": "boolean"}` |
| fallback_used | да | нет | `{"type": "boolean"}` |
| selected_evidence_id | да | да | `{"anyOf": [{"pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$", "type": "string"}, {"type": "null"}]}` |
| final_attempt_id | да | да | `{"anyOf": [{"pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$", "type": "string"}, {"type": "null"}]}` |
| attempt_ids | да | нет | `{"items": {"pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$", "type": "string"}, "type": "array"}` |
| attempts | да | нет | `{"items": {"$ref": "#/$defs/FetchAttempt"}, "type": "array"}` |
| redirect_chain | да | нет | `{"items": {"$ref": "#/$defs/RedirectHop"}, "type": "array"}` |
| evidence | да | нет | `{"items": {"$ref": "#/$defs/EvidenceRecord"}, "type": "array"}` |
| errors | да | нет | `{"items": {"$ref": "#/$defs/ErrorDetail"}, "type": "array"}` |
| reason_codes | да | нет | `{"items": {"enum": ["INPUT_URL_INVALID", "INPUT_SCHEME_UNSUPPORTED", "INPUT_CREDENTIALS_FORBIDDEN", "INPUT_PORT_INVALID", "INPUT_ARTIFACT_INVALID", "TARGET_NOT_PUBLIC", "TARGET_DNS_REBINDING", "TARGET_PORT_FORBIDDEN", "TARGET_PATH_FORBIDDEN", "TARGET_SECURITY_UNAVAILABLE", "DNS_NXDOMAIN", "DNS_TEMPORARY", "NETWORK_CONNECT_ERROR", "NETWORK_READ_ERROR", "NETWORK_TIMEOUT", "NETWORK_CANCELLED", "HTTPS_TLS_CERTIFICATE_ERROR", "HTTPS_TLS_HANDSHAKE_ERROR", "HTTPS_DOWNGRADE", "REDIRECT_INVALID_LOCATION", "REDIRECT_LOOP", "REDIRECT_LIMIT", "REDIRECT_EXTERNAL_HOST", "FALLBACK_NO_USABLE_RESULT", "FALLBACK_USED", "HTTP_DENIED", "HTTP_MISSING", "HTTP_UNAVAILABLE", "HTTP_UNEXPECTED_STATUS", "CONTENT_EMPTY", "CONTENT_NON_HTML", "CONTENT_TOO_LARGE", "CONTENT_INCOMPLETE", "CONTENT_ENCODING_UNSUPPORTED", "CONTENT_DECODE_ERROR", "CONTENT_SUSPICIOUS", "CONTENT_JS_CANDIDATE", "ACCESS_LOGIN", "ACCESS_CHALLENGE", "BUDGET_DEADLINE", "BUDGET_ATTEMPTS", "RETRY_DEFERRED", "OUTPUT_WRITE_FAILED", "IMPLEMENTATION_NOT_AVAILABLE"], "type": "string"}, "type": "array"}` |
| proposed_target_url | да | да | `{"anyOf": [{"format": "uri", "pattern": "^https?://", "type": "string"}, {"type": "null"}]}` |
| next_action | да | нет | `{"minLength": 1, "type": "string"}` |
| budget | да | нет | `{"$ref": "#/$defs/BudgetSnapshot"}` |

## SiteValidationResult / BudgetSnapshot

| Поле | Обязательно | null | Тип / ограничения |
|---|---|---|---|
| schema_version | да | нет | `{"enum": ["1.0.0", "1.1.0"], "type": "string"}` |
| started_at | да | нет | `{"format": "date-time", "type": "string"}` |
| captured_at | да | нет | `{"format": "date-time", "type": "string"}` |
| step0_deadline_at | да | нет | `{"format": "date-time", "type": "string"}` |
| total_deadline_at | да | нет | `{"format": "date-time", "type": "string"}` |
| elapsed_seconds | да | нет | `{"minimum": 0, "type": "number"}` |
| step0_attempts | да | нет | `{"minimum": 0, "type": "integer"}` |
| total_attempts | да | нет | `{"minimum": 0, "type": "integer"}` |
| step0_max_attempts | да | нет | `{"minimum": 1, "type": "integer"}` |
| total_max_attempts | да | нет | `{"minimum": 1, "type": "integer"}` |

## SiteValidationResult / ErrorDetail

| Поле | Обязательно | null | Тип / ограничения |
|---|---|---|---|
| code | да | нет | `{"enum": ["INPUT_URL_INVALID", "INPUT_SCHEME_UNSUPPORTED", "INPUT_CREDENTIALS_FORBIDDEN", "INPUT_PORT_INVALID", "INPUT_ARTIFACT_INVALID", "TARGET_NOT_PUBLIC", "TARGET_DNS_REBINDING", "TARGET_PORT_FORBIDDEN", "TARGET_PATH_FORBIDDEN", "TARGET_SECURITY_UNAVAILABLE", "DNS_NXDOMAIN", "DNS_TEMPORARY", "NETWORK_CONNECT_ERROR", "NETWORK_READ_ERROR", "NETWORK_TIMEOUT", "NETWORK_CANCELLED", "HTTPS_TLS_CERTIFICATE_ERROR", "HTTPS_TLS_HANDSHAKE_ERROR", "HTTPS_DOWNGRADE", "REDIRECT_INVALID_LOCATION", "REDIRECT_LOOP", "REDIRECT_LIMIT", "REDIRECT_EXTERNAL_HOST", "FALLBACK_NO_USABLE_RESULT", "FALLBACK_USED", "HTTP_DENIED", "HTTP_MISSING", "HTTP_UNAVAILABLE", "HTTP_UNEXPECTED_STATUS", "CONTENT_EMPTY", "CONTENT_NON_HTML", "CONTENT_TOO_LARGE", "CONTENT_INCOMPLETE", "CONTENT_ENCODING_UNSUPPORTED", "CONTENT_DECODE_ERROR", "CONTENT_SUSPICIOUS", "CONTENT_JS_CANDIDATE", "ACCESS_LOGIN", "ACCESS_CHALLENGE", "BUDGET_DEADLINE", "BUDGET_ATTEMPTS", "RETRY_DEFERRED", "OUTPUT_WRITE_FAILED", "IMPLEMENTATION_NOT_AVAILABLE"], "type": "string"}` |
| category | да | нет | `{"enum": ["input", "target", "transport", "http", "content", "budget", "output", "implementation"], "type": "string"}` |
| message | да | нет | `{"minLength": 1, "type": "string"}` |
| retryable | да | нет | `{"type": "boolean"}` |
| attempt_id | да | да | `{"anyOf": [{"pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$", "type": "string"}, {"type": "null"}]}` |

## SiteValidationResult / EvidenceFile

| Поле | Обязательно | null | Тип / ограничения |
|---|---|---|---|
| path | да | нет | `{"type": "string"}` |
| sha256 | да | нет | `{"pattern": "^[a-f0-9]{64}$", "type": "string"}` |
| size_bytes | да | нет | `{"minimum": 0, "type": "integer"}` |
| representation | да | нет | `{"enum": ["content_encoded", "entity_bytes", "decoded_text", "attempt_observation"], "type": "string"}` |
| complete | да | нет | `{"type": "boolean"}` |

## SiteValidationResult / EvidenceRecord

| Поле | Обязательно | null | Тип / ограничения |
|---|---|---|---|
| schema_version | да | нет | `{"enum": ["1.0.0", "1.1.0"], "type": "string"}` |
| evidence_id | да | нет | `{"pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$", "type": "string"}` |
| attempt_id | да | нет | `{"pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$", "type": "string"}` |
| requested_url | да | нет | `{"format": "uri", "pattern": "^https?://", "type": "string"}` |
| captured_at | да | нет | `{"format": "date-time", "type": "string"}` |
| files | да | нет | `{"items": {"$ref": "#/$defs/EvidenceFile"}, "minItems": 1, "type": "array"}` |
| declared_encoding | да | да | `{"anyOf": [{"type": "string"}, {"type": "null"}]}` |
| applied_encoding | да | да | `{"anyOf": [{"type": "string"}, {"type": "null"}]}` |
| replacement_characters | да | нет | `{"minimum": 0, "type": "integer"}` |
| sniffed_html | да | нет | `{"type": "boolean"}` |
| classification_basis | да | нет | `{"items": {"type": "string"}, "type": "array"}` |
| classification_confidence | да | да | `{"anyOf": [{"maximum": 1, "minimum": 0, "type": "number"}, {"type": "null"}]}` |
| redacted_headers | да | нет | `{"items": {"type": "string"}, "type": "array"}` |

## SiteValidationResult / FetchAttempt

| Поле | Обязательно | null | Тип / ограничения |
|---|---|---|---|
| schema_version | да | нет | `{"enum": ["1.0.0", "1.1.0"], "type": "string"}` |
| attempt_id | да | нет | `{"pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$", "type": "string"}` |
| parent_attempt_id | да | да | `{"anyOf": [{"pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$", "type": "string"}, {"type": "null"}]}` |
| requested_url | да | нет | `{"format": "uri", "pattern": "^https?://", "type": "string"}` |
| purpose | да | нет | `{"enum": ["initial", "retry", "http_fallback", "redirect"], "type": "string"}` |
| request_sent | да | нет | `{"type": "boolean"}` |
| resolved_target | да | да | `{"anyOf": [{"$ref": "#/$defs/ResolvedTarget"}, {"type": "null"}]}` |
| status_code | да | да | `{"anyOf": [{"maximum": 599, "minimum": 100, "type": "integer"}, {"type": "null"}]}` |
| response_headers | да | нет | `{"items": {"$ref": "#/$defs/Header"}, "type": "array"}` |
| started_at | да | нет | `{"format": "date-time", "type": "string"}` |
| elapsed_ms | да | нет | `{"minimum": 0, "type": "number"}` |
| transport_error | да | да | `{"anyOf": [{"$ref": "#/$defs/ErrorDetail"}, {"type": "null"}]}` |
| tls_state | да | нет | `{"enum": ["not_observed", "not_applicable", "verified", "certificate_error", "handshake_error"], "type": "string"}` |
| evidence_id | да | нет | `{"pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$", "type": "string"}` |
| bytes_read | да | нет | `{"minimum": 0, "type": "integer"}` |
| decoded_bytes | да | нет | `{"minimum": 0, "type": "integer"}` |
| body_complete | да | нет | `{"type": "boolean"}` |

## SiteValidationResult / Header

| Поле | Обязательно | null | Тип / ограничения |
|---|---|---|---|
| name | да | нет | `{"enum": ["content-type", "content-length", "content-encoding", "location", "retry-after", "server", "date", "vary", "cache-control", "etag", "last-modified"], "type": "string"}` |
| value | да | нет | `{"type": "string"}` |
| redacted | да | нет | `{"type": "boolean"}` |

## SiteValidationResult / RedirectHop

| Поле | Обязательно | null | Тип / ограничения |
|---|---|---|---|
| schema_version | да | нет | `{"enum": ["1.0.0", "1.1.0"], "type": "string"}` |
| source_attempt_id | да | нет | `{"pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$", "type": "string"}` |
| source_url | да | нет | `{"format": "uri", "pattern": "^https?://", "type": "string"}` |
| status_code | да | нет | `{"enum": [301, 302, 303, 307, 308], "type": "integer"}` |
| raw_location | да | да | `{"anyOf": [{"type": "string"}, {"type": "null"}]}` |
| target_url | да | да | `{"anyOf": [{"format": "uri", "pattern": "^https?://", "type": "string"}, {"type": "null"}]}` |
| decision | да | нет | `{"enum": ["follow", "allow_www_alias", "needs_input", "reject"], "type": "string"}` |
| reason_code | да | да | `{"anyOf": [{"enum": ["INPUT_URL_INVALID", "INPUT_SCHEME_UNSUPPORTED", "INPUT_CREDENTIALS_FORBIDDEN", "INPUT_PORT_INVALID", "INPUT_ARTIFACT_INVALID", "TARGET_NOT_PUBLIC", "TARGET_DNS_REBINDING", "TARGET_PORT_FORBIDDEN", "TARGET_PATH_FORBIDDEN", "TARGET_SECURITY_UNAVAILABLE", "DNS_NXDOMAIN", "DNS_TEMPORARY", "NETWORK_CONNECT_ERROR", "NETWORK_READ_ERROR", "NETWORK_TIMEOUT", "NETWORK_CANCELLED", "HTTPS_TLS_CERTIFICATE_ERROR", "HTTPS_TLS_HANDSHAKE_ERROR", "HTTPS_DOWNGRADE", "REDIRECT_INVALID_LOCATION", "REDIRECT_LOOP", "REDIRECT_LIMIT", "REDIRECT_EXTERNAL_HOST", "FALLBACK_NO_USABLE_RESULT", "FALLBACK_USED", "HTTP_DENIED", "HTTP_MISSING", "HTTP_UNAVAILABLE", "HTTP_UNEXPECTED_STATUS", "CONTENT_EMPTY", "CONTENT_NON_HTML", "CONTENT_TOO_LARGE", "CONTENT_INCOMPLETE", "CONTENT_ENCODING_UNSUPPORTED", "CONTENT_DECODE_ERROR", "CONTENT_SUSPICIOUS", "CONTENT_JS_CANDIDATE", "ACCESS_LOGIN", "ACCESS_CHALLENGE", "BUDGET_DEADLINE", "BUDGET_ATTEMPTS", "RETRY_DEFERRED", "OUTPUT_WRITE_FAILED", "IMPLEMENTATION_NOT_AVAILABLE"], "type": "string"}, {"type": "null"}]}` |
| observed_at | да | нет | `{"format": "date-time", "type": "string"}` |
| psl_sha256 | да | да | `{"anyOf": [{"pattern": "^[a-f0-9]{64}$", "type": "string"}, {"type": "null"}]}` |

## SiteValidationResult / ResolvedTarget

| Поле | Обязательно | null | Тип / ограничения |
|---|---|---|---|
| host | да | нет | `{"type": "string"}` |
| port | да | нет | `{"maximum": 65535, "minimum": 1, "type": "integer"}` |
| addresses | да | нет | `{"items": {"type": "string"}, "minItems": 1, "type": "array"}` |
| connected_ip | да | да | `{"anyOf": [{"type": "string"}, {"type": "null"}]}` |
| enforcement | да | нет | `{"enum": ["pinned", "controlled_egress"], "type": "string"}` |

## Step1Input / Step1Input

| Поле | Обязательно | null | Тип / ограничения |
|---|---|---|---|
| schema_version | да | нет | `{"enum": ["1.0.0", "1.1.0"], "type": "string"}` |
| source_step | да | нет | `{"const": 0, "type": "integer"}` |
| site | да | нет | `{"$ref": "#/$defs/SiteValidationResult"}` |
| selected_response | да | нет | `{"$ref": "#/$defs/FetchAttempt"}` |
| selected_evidence | да | нет | `{"$ref": "#/$defs/EvidenceRecord"}` |
| budget | да | нет | `{"$ref": "#/$defs/BudgetSnapshot"}` |
| crawl_permission | да | нет | `{"const": "unknown", "type": "string"}` |
