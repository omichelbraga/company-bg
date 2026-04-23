# Technical Implementation Plan — CompanyBG API Teams Background Feature

**Repo:** `omichelbraga/company-bg`  
**Local path:** `/home/michelbragaguimaraes/Documents/Projects/photo-bg-tool`  
**Branch target:** `feature/tbg-entra-lookup`  
**Date:** 2026-04-23

---

## 1. Objective

Add an optional Teams background generation path to the existing `company-bg` FastAPI service.

The new behavior must:

- keep the current photo workflow unchanged by default,
- trigger only when `tbg=true` is included in the request,
- use the submitted email to query Entra ID through Microsoft Graph,
- retrieve `displayName` and `jobTitle`,
- populate SVG templates stored in a new `tbg/` folder,
- render those SVGs to PNG,
- return those PNGs alongside the normal photo outputs.

No deployment changes in this phase. Local-first only.

---

## 2. Current Architecture Summary

Current service entrypoint:
- `microservice.py`

Current major flow:
1. `POST /process-image/` accepts image + `name` + `email`
2. request is queued in memory
3. `_process_job()` performs:
   - image open
   - face detection
   - background removal
   - portrait build
   - composite onto all PNGs in `backgrounds/`
4. outputs are saved under:
   - `out_images/{email_slug}/`
5. `GET /status/{job_id}` returns state + image URLs

Important current characteristics:
- jobs are stored in-memory only
- processing is serialized via `ThreadPoolExecutor(max_workers=1)`
- outputs are static files served from `/images`
- current status payload only understands standard photo outputs

---

## 3. Design Principles

- **Default-safe:** no behavior change unless `tbg=true`
- **Backward-compatible:** existing clients must continue working
- **Partial success allowed:** Teams background generation failure should not kill normal photo generation
- **Minimal invasive change:** preserve current architecture where practical
- **Local validation first:** do not optimize for deployment before correctness

---

## 4. Proposed File/Folder Changes

## 4.1 New folders

```text
company-bg/
├── tbg/                        # SVG template folder
├── docs/
│   └── TECHNICAL_PLAN_TBG_ENTRA.md
```

## 4.2 Likely new Python modules

Recommended additions:

```text
company-bg/
├── graph_client.py             # Entra / Microsoft Graph lookup helpers
├── tbg_processor.py            # SVG placeholder replacement + PNG rendering
```

This keeps `microservice.py` from turning into a kitchen sink with auth, image processing, templating, Graph calls, and existential despair all in one file.

---

## 5. API Contract Changes

## 5.1 POST /process-image/

### Current fields
- `file`
- `image_url`
- `name`
- `email`

### Proposed new optional field
- `tbg`

### Proposed request example

```bash
curl -X POST http://localhost:8001/process-image/ \
  -H "Authorization: Bearer <TOKEN>" \
  -F "file=@photo.jpg" \
  -F "name=John Smith" \
  -F "email=john.smith@san-marcos.net" \
  -F "tbg=true"
```

### Parsing rule
Treat these as true:
- `true`
- `1`
- `yes`
- `on`

Everything else = false.

Recommended implementation: helper function such as:

```python
def parse_bool(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
```

---

## 5.2 GET /status/{job_id}

### Current response

```json
{
  "request_id": "abcd1234",
  "job_id": "...",
  "status": "done",
  "image_urls": ["/images/jsmith/JohnSmith-01.png"]
}
```

### Proposed extended response

```json
{
  "request_id": "abcd1234",
  "job_id": "...",
  "status": "done",
  "image_urls": ["/images/jsmith/JohnSmith-01.png"],
  "teams_backgrounds": {
    "requested": true,
    "status": "done",
    "image_urls": [
      "/images/jsmith/teams-backgrounds/tbg1.png",
      "/images/jsmith/teams-backgrounds/tbg2.png"
    ],
    "warning": null,
    "error": null
  }
}
```

### Possible `teams_backgrounds.status` values
- `not_requested`
- `skipped`
- `processing`
- `done`
- `failed`

Backward compatibility note:
- existing clients can ignore the new field safely

---

## 6. Job Model Changes

Current in-memory job structure:

```python
jobs[job_id] = {
    "status": "queued",
    "created_at": datetime.now(),
    "image_urls": None,
    "error": None,
}
```

### Proposed structure

```python
jobs[job_id] = {
    "status": "queued",
    "created_at": datetime.now(),
    "image_urls": None,
    "error": None,
    "tbg_requested": False,
    "tbg_status": "not_requested",
    "tbg_image_urls": None,
    "tbg_warning": None,
    "tbg_error": None,
}
```

This gives us partial success without inventing nonsense later.

---

## 7. Processing Flow Changes

## 7.1 Existing flow stays first

The current photo pipeline should continue to run first, because that is the known-good behavior.

## 7.2 Proposed new `_process_job()` sequence

1. Set overall job status to `processing`
2. Open image and validate face
3. Run background removal
4. Build portrait
5. Generate normal photo outputs into `out_images/{email_slug}/`
6. If `tbg_requested`:
   - set `tbg_status = processing`
   - query Graph using email
   - load SVG templates from `tbg/`
   - replace placeholders
   - render PNGs into `out_images/{email_slug}/teams-backgrounds/`
   - set `tbg_status = done`
7. If Teams generation fails:
   - preserve normal photo outputs
   - set `tbg_status = failed`
   - set `tbg_error`
8. Set overall job status to `done` if normal photos succeeded

### Important behavior rule
Overall job should be considered **done** if the core photo pipeline succeeded, even if TBG failed.

Why: Mike explicitly wants Teams generation to be optional, not a grenade wired into the main path.

---

## 8. Microsoft Graph Integration

## 8.1 Required environment variables

Add to `.env.example`:

```env
GRAPH_TENANT_ID=
GRAPH_CLIENT_ID=
GRAPH_CLIENT_SECRET=
GRAPH_USER_LOOKUP_FIELD=mail
```

Optional:

```env
GRAPH_TIMEOUT_SECONDS=15
```

## 8.2 Authentication model

Use client credentials flow against Microsoft identity platform.

### Token endpoint

```text
https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token
```

### Scope

```text
https://graph.microsoft.com/.default
```

## 8.3 Graph lookup strategy

Recommended lookup sequence:

### Primary
Try direct user lookup via filter on `mail`

Example concept:

```http
GET /v1.0/users?$filter=mail eq 'user@domain.com'&$select=displayName,jobTitle,mail,userPrincipalName
```

### Fallback
If no result, try `userPrincipalName`

```http
GET /v1.0/users?$filter=userPrincipalName eq 'user@domain.com'&$select=displayName,jobTitle,mail,userPrincipalName
```

## 8.4 Required fields

Store and return:
- `displayName`
- `jobTitle`

### Fallback rules
- if `displayName` missing → fail Teams generation
- if `jobTitle` missing → use empty string

## 8.5 Suggested helper interface

```python
def get_user_profile_by_email(email: str) -> dict:
    return {
        "display_name": "John Smith",
        "job_title": "IT Program Manager",
        "source": "mail"
    }
```

---

## 9. SVG Template Processing

## 9.1 Input templates

Templates will be stored in `tbg/`.

Suggested normalized filenames after import:
- `tbg1.svg`
- `tbg2.svg`
- `tbg3.svg`
- ...

The supplied files currently arrived as `.xml`, but they are SVG content and should be normalized in repo storage.

## 9.2 Placeholder replacement

Required replacements:
- `{{DisplayName}}`
- `{{JobTitle}}`

Implementation should use exact string replacement, not regex wizardry unless necessary.

## 9.3 XML safety

Because these are controlled design templates in-repo, plain template substitution is fine.

Need to XML-escape inserted values safely:
- `&`
- `<`
- `>`
- quotes if relevant

Recommended helper:

```python
from xml.sax.saxutils import escape
```

## 9.4 Output naming

Generated outputs:

```text
out_images/{email_slug}/teams-backgrounds/tbg1.png
out_images/{email_slug}/teams-backgrounds/tbg2.png
...
```

---

## 10. SVG → PNG Rendering

## 10.1 Recommended library

Use **CairoSVG** first.

Why:
- straightforward Python integration
- good for static SVG → PNG rendering
- no browser process circus
- suitable for local and containerized use

## 10.2 New dependency

Add to `requirements.txt`:

```text
cairosvg
```

## 10.3 Rendering helper interface

```python
def render_svg_to_png(svg_text: str, output_path: str) -> None:
    ...
```

## 10.4 Font caveat

The templates reference `ScalaSans Caps` and `ScalaSansCaps-Bold`.

Need local validation for:
- whether those fonts exist on Mike's machine,
- whether CairoSVG respects them,
- whether fallback fonts are visually acceptable.

If fonts are missing, options are:
1. install required fonts in local/container runtime,
2. accept fallback font for initial testing,
3. later convert templates to use available fonts.

This is the most likely visual blocker.

---

## 11. Code-Level Implementation Plan

## 11.1 `microservice.py`

### Changes needed
- add `tbg: str = Form(None)` to `POST /process-image/`
- parse boolean
- store TBG fields in job state
- pass `tbg_requested` into background worker
- extend `/status/{job_id}` response with Teams metadata

### Suggested helper additions
- `parse_bool()`

## 11.2 `graph_client.py` (new)

Responsibilities:
- obtain Graph token
- query user by email
- normalize return payload
- raise meaningful exceptions

Suggested exceptions:
- `GraphAuthError`
- `GraphUserNotFoundError`
- `GraphRequestError`

## 11.3 `tbg_processor.py` (new)

Responsibilities:
- list/load SVG templates from `tbg/`
- replace placeholders
- XML escape values
- render PNGs
- return generated output URLs

Suggested public function:

```python
def generate_teams_backgrounds(email_slug: str, display_name: str, job_title: str) -> list[str]:
    ...
```

## 11.4 `_process_job()`

Modify to call Teams generation only after normal photo outputs succeed.

Pseudo-flow:

```python
normal_urls = generate_standard_outputs(...)

if tbg_requested:
    try:
        profile = get_user_profile_by_email(email)
        tbg_urls = generate_teams_backgrounds(
            email_slug=email_slug,
            display_name=profile["display_name"],
            job_title=profile["job_title"],
        )
        job["tbg_status"] = "done"
        job["tbg_image_urls"] = tbg_urls
    except Exception as e:
        job["tbg_status"] = "failed"
        job["tbg_error"] = str(e)
```

---

## 12. Environment and Deployment Prep

## 12.1 `.env.example`

Add Graph variables.

## 12.2 Dockerfile

If CairoSVG requires extra system packages, Dockerfile may later need updates such as:
- cairo-related libs
- libffi / shared libs depending on runtime behavior

This should be deferred until after local proof.

## 12.3 Portainer

No changes in this phase.

But eventual deployment checklist will need:
- Graph env vars added
- potentially font availability addressed
- feature branch merged or referenced appropriately

---

## 13. Local Validation Plan

## 13.1 Baseline regression

Before feature work:
- confirm current `/process-image/` still works unchanged on branch

## 13.2 TBG functional tests

### Test 1 — No TBG
- request without `tbg`
- verify unchanged output behavior

### Test 2 — TBG enabled with valid user
- request with `tbg=true`
- verify normal photo outputs
- verify Teams background PNG generation
- verify `/status/{job_id}` includes `teams_backgrounds`

### Test 3 — Unknown email
- request with `tbg=true`
- verify normal photo outputs still complete
- verify `teams_backgrounds.status = failed`

### Test 4 — Missing job title
- use test user with blank job title if possible
- verify background still renders cleanly

### Test 5 — All templates render
- verify every provided template yields a PNG

### Test 6 — Output URL structure
- verify static files are reachable under `/images/.../teams-backgrounds/...`

---

## 14. Curl Test Examples

## Normal flow

```bash
curl -X POST http://localhost:8001/process-image/ \
  -H "Authorization: Bearer <TOKEN>" \
  -F "file=@photo.jpg" \
  -F "name=John Smith" \
  -F "email=john.smith@san-marcos.net"
```

## TBG enabled

```bash
curl -X POST http://localhost:8001/process-image/ \
  -H "Authorization: Bearer <TOKEN>" \
  -F "file=@photo.jpg" \
  -F "name=John Smith" \
  -F "email=john.smith@san-marcos.net" \
  -F "tbg=true"
```

## Poll status

```bash
curl http://localhost:8001/status/<job_id> \
  -H "Authorization: Bearer <TOKEN>"
```

---

## 15. Risks and Blockers

## 15.1 Highest risk — font rendering
The SVGs reference specific fonts that may not exist in the runtime.

Impact:
- output may render with fallback fonts
- text layout may shift

## 15.2 Graph permission setup
Need app registration with the correct permissions and consent.

Impact:
- feature cannot be fully validated until credentials exist

## 15.3 Dependency packaging
CairoSVG may require runtime/system packages, especially in Docker.

Impact:
- local Python may work before container build does

## 15.4 Current API is in-memory only
Jobs and state are not persistent.

Impact:
- acceptable for current scope
- not a blocker for this feature

---

## 16. Recommended Implementation Order

1. Create feature branch
2. Add technical plan doc
3. Add `tbg/` template folder with normalized SVG filenames
4. Add Graph env variables to `.env.example`
5. Implement `graph_client.py`
6. Implement `tbg_processor.py`
7. Extend `microservice.py` request parsing and status response
8. Validate locally
9. Only then consider Portainer deployment

---

## 17. Success Criteria

This implementation phase is successful when:

- current photo flow still works unchanged without `tbg`
- Graph lookup works by email
- `displayName` and `jobTitle` populate templates correctly
- all TBG templates render to PNG
- Teams backgrounds are returned as additional outputs
- TBG failure does not break photo generation
- Mike can test the feature locally on LAN before deployment

---

**End of technical plan**
