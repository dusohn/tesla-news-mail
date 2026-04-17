# TSLA Finviz Daily Digest

`Finviz`에서 `TSLA` 뉴스 헤드라인을 가져와 최근 24시간 기사만 추리고, 중복을 제거한 뒤 요약 메일을 보내는 스크립트입니다.

`GitHub Actions`에서 매일 아침 자동 실행할 수 있도록 워크플로도 포함되어 있습니다.

## 포함 파일

- `tsla_finviz_digest.py`: 뉴스 수집, 중복 제거, 요약, 메일 발송
- `.env.example`: 설정 예시
- `run_digest.bat`: 실행용 배치 파일
- `register_task.ps1`: Windows 작업 스케줄러 등록 스크립트
- `test_digest.py`: 샘플 HTML 기반 테스트
- `.github/workflows/daily-digest.yml`: GitHub Actions 매일 실행 설정

## 설정 방법

1. `.env.example`를 복사해서 `.env`로 이름을 바꿉니다.
2. 메일 발송 정보를 채웁니다.

예시:

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USE_SSL=true
SMTP_USERNAME=your_email@gmail.com
SMTP_PASSWORD=your_app_password
EMAIL_FROM=your_email@gmail.com
EMAIL_TO=receiver@example.com
```

`Gmail`을 쓸 경우 일반 비밀번호 대신 앱 비밀번호를 쓰는 편이 안전합니다.

## 수동 실행

```powershell
& "C:\Users\dusoh\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\tsla_finviz_digest.py --dry-run
```

메일을 실제 발송하려면 `--dry-run` 없이 실행하면 됩니다.

## 매일 아침 등록

매일 오전 8시에 실행:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\register_task.ps1 -Time "08:00"
```

등록 후 Windows 작업 스케줄러에서 `TSLA Finviz Digest` 작업으로 확인할 수 있습니다.

## GitHub Actions로 실행

`GitHub`에서 매일 오전 8시 `KST`에 실행되도록 워크플로가 들어 있습니다.

현재 워크플로의 cron은 `0 23 * * *`이며, 이는 `UTC 23:00`, 한국 시간으로는 다음 날 오전 8시입니다.

저장소 `Settings -> Secrets and variables -> Actions`에 아래 시크릿을 추가하면 됩니다.

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USE_SSL`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `EMAIL_FROM`
- `EMAIL_TO`

선택 시크릿:

- `TICKER` 기본값은 `TSLA`
- `LOOKBACK_HOURS` 기본값은 `24`
- `FINVIZ_TIMEZONE` 기본값은 `America/New_York`
- `DIGEST_TIMEZONE` 기본값은 `Asia/Seoul`

수동 실행은 `Actions -> Tesla News Mail -> Run workflow`에서 가능합니다.

## 동작 방식

- `Finviz`의 `TSLA` 뉴스 테이블을 읽습니다.
- 최근 24시간 기사만 남깁니다.
- 제목/링크 유사도로 중복 기사를 제거합니다.
- 주요 이슈와 자주 나온 키워드를 뽑아 메일 본문에 넣습니다.
- 대표 헤드라인과 원문 링크를 함께 보냅니다.

## 테스트

```powershell
& "C:\Users\dusoh\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" .\test_digest.py
```
