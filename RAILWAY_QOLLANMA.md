# Mini App'ni internetga chiqarish — Railway orqali (bepul)

Bu qo'llanma sizga hech qanday buyruq qatori (terminal) buyruqlarisiz, faqat brauzer orqali botni 24/7 ishlaydigan qilishni ko'rsatadi.

## 1-QADAM: GitHub'da hisob oching

1. [github.com](https://github.com) ga o'ting
2. **Sign up** tugmasini bosing, email va parol bilan ro'yxatdan o'ting

## 2-QADAM: Yangi repozitoriy (papka) yarating

1. Yuqori o'ng burchakda **"+"** belgisini bosing → **"New repository"**
2. Nom bering: `chorva-bozor`
3. **"Public"** ni tanlang (bepul reja uchun)
4. **"Create repository"** tugmasini bosing

## 3-QADAM: Fayllarni yuklash

1. Yaratilgan bo'sh repozitoriy sahifasida **"uploading an existing file"** havolasini bosing
2. Menga yuborilgan **chorva_miniapp** papkasidagi barcha fayllarni (app.py, requirements.txt, Procfile, va templates papkasi ichidagi index.html) shu yerga sudrab tashlang
   - Muhim: `templates` papkasini ham fayl sifatida yuklash uchun, avval shu papkani ochib, `index.html` faylini alohida yuklang, GitHub o'zi `templates/index.html` yo'lini saqlab qoladi agar siz papka strukturasini saqlab yuklasangiz (zamonaviy brauzerlarda papkani ham sudrab tashlasa bo'ladi)
3. Pastda **"Commit changes"** tugmasini bosing

## 4-QADAM: Railway'da hisob oching

1. [railway.app](https://railway.app) ga o'ting
2. **"Login"** → **"Login with GitHub"** orqali kiring (GitHub hisobingiz bilan bog'lanadi)

## 5-QADAM: Loyihani joylashtirish

1. **"New Project"** tugmasini bosing
2. **"Deploy from GitHub repo"** ni tanlang
3. `chorva-bozor` repozitoriyangizni tanlang
4. Railway avtomatik ravishda kodni o'qib, joylashtirishni boshlaydi

## 6-QADAM: Muhim o'zgaruvchilarni kiritish

1. Loyiha ochilgach, **"Variables"** bo'limiga o'ting
2. Quyidagilarni qo'shing (**"New Variable"** tugmasi orqali):
   - `BOT_TOKEN` = sizning bot tokeningiz
   - `ADMIN_ID` = sizning Telegram ID'ingiz (766725960)
3. Har birini kiritgach saqlang — Railway avtomatik qayta joylashtiradi

## 7-QADAM: Havolani olish

1. **"Settings"** bo'limiga o'ting
2. **"Networking"** qismida **"Generate Domain"** tugmasini bosing
3. Sizga shunday havola beriladi: `https://chorva-bozor-production.up.railway.app`
4. Shu havolani nusxalab oling

## 8-QADAM: Mini App havolasini botga ulash

1. Yana **"Variables"** bo'limiga qayting
2. Yangi o'zgaruvchi qo'shing: `MINI_APP_URL` = 7-qadamda olgan havola
3. Saqlang — Railway avtomatik qayta ishga tushiradi

## 9-QADAM: Tekshirish

1. Brauzerda 7-qadamdagi havolani oching — Chorva Bozor katalogi ko'rinishi kerak
2. Telegram'da botga `/start` yuboring — endi **"📸 Katalogni ochish"** tugmasi ham chiqishi kerak
3. Shu tugmani bosing — Mini App Telegram ichida ochilishi kerak

---

## Muhim eslatma

Endi bot **Railway serverida** ishlayapti, ya'ni noutbukingizni yopsangiz ham bot va Mini App ishlab turadi. Noutbukdagi eski botni (agar hali ishlab tursa) endi to'xtatsangiz bo'ladi — ikkalasi bir vaqtda ishlasa, ular bir-biriga xalaqit berishi mumkin.

## Muammo bo'lsa

Railway'dagi loyiha sahifasida **"Deployments"** bo'limida qizil "Failed" yozuvi chiqsa, shu yerni bosib xatolik matnini (log) ko'ring va menga screenshot yuboring — birga tuzataman.
