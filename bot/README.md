# OANDA EMA/ATR Bot

บอทเทรด forex บนบัญชี OANDA practice รันบน GitHub Actions ทั้งหมดทำจากมือถือได้ ไม่ต้องมีคอม ไม่ต้องมี VPS

## กลยุทธ์

- แท่งเทียน H1 ใช้เฉพาะแท่งที่ปิดแล้ว
- EMA 20 ตัด EMA 50 ขึ้น → เปิด long, ตัดลง → เปิด short (ปิดได้ด้วย `ALLOW_SHORT=false`)
- Stop loss ห่างจากราคาเข้า 2 × ATR(14) และ trailing ตามไปด้วยระยะเท่ากัน
- ขนาดไม้คิดจาก `RISK_PCT` ของ NAV ต่อไม้ (ค่าเริ่มต้น 1%) เพดานที่ `MAX_UNITS`
- EMA ตัดกลับทาง → ปิดไม้เดิมแล้วเปิดฝั่งใหม่
- ทำงานทีละแท่ง จำแท่งล่าสุดที่จัดการแล้วไว้ใน `state.json` รันซ้ำกี่ครั้งก็ไม่เปิดไม้ซ้ำ

## ตั้งค่าจากมือถือ

1. สมัคร OANDA แล้วเปิด **practice account** (fxTrade Practice)
2. เข้า hub.oanda.com → **Manage API Access** → Generate token เก็บ token ไว้
3. จดเลขบัญชี practice รูปแบบ `101-xxx-xxxxxxx-xxx` (หน้าเดียวกัน หรือใน fxTrade)
4. GitHub → repo นี้ → Settings → Secrets and variables → Actions
   - **Secrets**: `OANDA_TOKEN`, `OANDA_ACCOUNT_ID`
   - (ถ้าอยากได้แจ้งเตือน) `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
   - **Variables** (ไม่ใส่ก็ได้ ใช้ค่าเริ่มต้น): `DRY_RUN`, `INSTRUMENT`, `GRANULARITY`, `RISK_PCT`, `MAX_UNITS`, `ALLOW_SHORT`, `EMA_FAST`, `EMA_SLOW`, `ATR_PERIOD`, `ATR_MULT`
5. Actions → **oanda-bot** → Run workflow กดรันมือดู log ก่อน
6. `DRY_RUN` เริ่มต้นเป็น `true` บอทจะพิมพ์ว่าจะทำอะไรแต่ไม่ส่งออเดอร์ พอใจแล้วตั้ง Variable `DRY_RUN=false`

ตารางเวลาอัตโนมัติ (ทุกชั่วโมงนาทีที่ 4) จะทำงานก็ต่อเมื่อ workflow อยู่บน default branch ของ repo ถ้ายังอยู่บน branch อื่น กดรันมือได้ หรือ merge เข้า default branch ก่อน

## Telegram

1. คุยกับ @BotFather → `/newbot` → ได้ token
2. ทักบอทตัวนั้นหนึ่งข้อความ แล้วเปิด `https://api.telegram.org/bot<TOKEN>/getUpdates` เอาเลข `chat.id`
3. ใส่สองค่านั้นเป็น secret ตามข้อ 4 ข้างบน

บอทส่งข้อความเฉพาะตอนมีการเปิด/ปิดไม้หรือออเดอร์ล้มเหลว รอบที่ไม่มีอะไรทำจะเงียบ

## Backtest

```
cd bot
OANDA_TOKEN=... OANDA_ACCOUNT_ID=... python backtest.py EUR_USD H1 3000
```

ผลเป็นหน่วย "ระยะราคา" รวมต่อ 1 unit ไม่รวม spread กับ swap ใช้เทียบ parameter กันเองพอ ไม่ใช่ตัวเลขกำไรจริง

## ไฟล์

| ไฟล์ | หน้าที่ |
| --- | --- |
| `oanda.py` | client REST v20: แท่งเทียน, ราคา, บัญชี, เปิด/ปิดไม้ |
| `strategy.py` | indicator, สัญญาณ, คำนวณขนาดไม้ |
| `main.py` | หนึ่งรอบตัดสินใจ: อ่านสัญญาณ → เทียบสถานะไม้ → สั่ง → แจ้งเตือน |
| `backtest.py` | ไล่กลยุทธ์บนข้อมูลย้อนหลัง |
| `../.github/workflows/bot.yml` | ตารางรันทุกชั่วโมง + เก็บ `state.json` ข้ามรอบ |
