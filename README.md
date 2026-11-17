# 내 트렌드 레이더

GitHub Actions가 야후 파이낸스에서 시세를 받아 `data.json`을 만들고, `index.html`이 그 데이터로 판정과 신호를 계산해 보여주는 홈페이지입니다.

## 처음 한 번만 설정 (약 5분)

1. GitHub에서 새 저장소를 만듭니다 (예: `trend-radar`, Public 권장 — 무료 계정은 Public이어야 Pages가 됩니다).
2. 이 폴더의 파일을 **폴더 구조 그대로** 올립니다. `.github/workflows/update.yml`이 꼭 포함돼야 합니다.
   - 웹에서 올릴 때: Add file → Upload files 에 폴더째 끌어다 놓기 (숨김 폴더 `.github`가 빠지지 않았는지 확인)
3. 저장소 **Settings → Pages → Build and deployment → Source** 를 **GitHub Actions** 로 바꿉니다.
4. **Actions** 탭 → "시세 갱신 + 페이지 배포" → **Run workflow** 를 한 번 눌러 첫 배포를 합니다.
5. 1~2분 뒤 `https://<내아이디>.github.io/<저장소이름>/` 에서 확인합니다.

이후에는 자동으로 돌아갑니다.
- 미국 장중(한국시간 밤 10시 30분경 ~ 새벽 5~6시) 30분마다 갱신
- 장 마감 뒤 한 번 더 갱신해서 일봉 확정
- GitHub 예약 실행은 몇 분~수십 분 늦어질 수 있습니다.
- 저장소에 60일 동안 아무 커밋이 없으면 GitHub이 예약 실행을 멈춥니다. 그럴 땐 Actions 탭에서 다시 켜 주세요.

## 종목 추가

`tickers.json`을 GitHub 웹에서 연필 아이콘으로 열어 한 줄 추가하고 Commit 하면, 바로 다시 배포됩니다.

```json
{"symbol": "NVDL", "name": "엔비디아 2배"}
```

심볼은 야후 파이낸스 기준입니다 (한국 종목은 `005930.KS`처럼).

## 내 신호 기록 남기기

`signals.csv`에 한 줄씩 추가합니다. 차트에 ◆로 표시되고 성과표에 따로 집계됩니다.

```
date,symbol,signal,memo
2026-11-10,SOXL,사,5분 S5 골든에 1차 진입
2026-11-12 14:35,SOXL,분할매도해,30분 약화
```

## 참고

- 시세는 야후 파이낸스(비공식 `yfinance`)라 지연되거나 가끔 빠질 수 있습니다. 실패하면 그 시간프레임은 이전 값을 씁니다.
- 1시간봉·5분봉 시간은 미국 동부시간입니다.
- 페이지의 [데이터 입력]으로 붙여넣은 데이터는 그 브라우저에만 저장됩니다. 자동 데이터가 있는 종목은 자동 데이터가 우선입니다.
- 판정과 신호는 참고용입니다. 주문은 직접 판단해서 하세요.

## 텔레그램 신호 알림

장 마감 뒤(미국장 06:50 KST 무렵, 국내장 15:45 KST 무렵) 그날 확정된 주요 신호
(돌파 진입·21일선 회복·SR flip·추가 매수·일부 익절·청산)를 텔레그램으로 보냅니다.
신호 계산은 `index.html` 코드를 그대로 써서(`scripts/alert.mjs`) 화면과 똑같습니다.

설정 (한 번만):
1. 텔레그램에서 @BotFather → `/newbot` 으로 봇을 만들고 토큰을 받습니다.
2. 만든 봇에게 아무 메시지나 보낸 뒤, 브라우저에서 `https://api.telegram.org/bot<토큰>/getUpdates` 를 열어 `"chat":{"id":숫자` 의 숫자를 확인합니다.
3. 저장소 Settings → Secrets and variables → Actions 에 `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` 두 개를 등록합니다.
4. Actions → "시세 갱신 + 페이지 배포" → Run workflow 에서 alert = `test` 로 실행하면 연결 확인 메시지가 옵니다.

비밀값이 없으면 알림 단계는 아무것도 보내지 않고 넘어갑니다.
