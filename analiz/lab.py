"""Laboratuvar arka ucu: parametre semasi, sablonlar, mod deposu (modlar.json), disa/ice aktarma.

Arayuz jeneriktir: PARAMETRELER listesinden sekme/surgu cizer. Motor parametreleri DionysosMotoru.p ile
ayni kimliklidir; "hassasiyet" ve "send_hz" motorda degil, Dinleyici ve gonderim hizinda uygulanir (controller).
Yalniz standart kutuphane + dionysos.VARSAYILAN.
"""
import copy
import json
import math
import os
import re
import threading
import uuid
from datetime import datetime

from .dionysos import VARSAYILAN

SEKMELER = ("parlaklik", "renk", "olaylar", "vokal", "vurus", "gonderim")
MOTOR_DISI = ("hassasiyet", "send_hz")     # motorun p sozlugunde olmayan, panelin uyguladigi parametreler
AD_MAX = 40
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
DOSYA_MAX = 1 << 20                         # ice aktarilan dosya ust siniri (1 MB)


def _p(id, etiket, sekme, seviye, min, max, adim, birim, aciklama, cizgi=None, tip="sayi", varsayilan=None):
    if varsayilan is None:
        varsayilan = VARSAYILAN[id]
    if tip == "anahtar":
        varsayilan = 1 if varsayilan else 0
        min, max, adim = 0, 1, 1
    return {"id": id, "etiket": etiket, "sekme": sekme, "seviye": seviye, "tip": tip,
            "min": min, "max": max, "adim": adim, "birim": birim, "varsayilan": varsayilan,
            "aciklama": aciklama, "cizgi": cizgi}


def _a(id, etiket, sekme, seviye, aciklama):
    return _p(id, etiket, sekme, seviye, 0, 1, 1, "", aciklama, tip="anahtar")


B, A = "basic", "advanced"

PARAMETRELER = [
    # ---------------- parlaklik ----------------
    _p("taban", "En düşük parlaklık", "parlaklik", B, 0.0, 0.3, 0.01, "",
       "Sessiz ama çalan müzikte ışığın düşebileceği en alt seviye (0 = tamamen kararabilir, 1 = tam parlak)."),
    _p("tavan", "En yüksek parlaklık", "parlaklik", B, 0.3, 1.0, 0.01, "",
       "Müzik en gürken ışığın çıkacağı seviye. Vuruşlar ve olaylar bunun da üstüne çıkabilir."),
    _p("dusus", "Sönüm hızı", "parlaklik", B, 0.05, 1.0, 0.01, "",
       "Ses azalınca ışığın ne kadar çabuk sönmesi gerektiği. Küçük değer yumuşak, büyük değer keskin söner."),
    _p("asagi_dim", "Hüzünlü kısımda kısılma", "parlaklik", A, 0.1, 1.0, 0.05, "x",
       "Vokal duygusu düşünce ışığın bu orana kadar kısılması (1 = kısılma yok)."),
    _p("asagi_dim_sn", "Kısılma süresi", "parlaklik", A, 0.05, 1.0, 0.05, "sn",
       "Kısılmaya ne kadar sürede inileceği."),
    _p("asagi_bekle_sn", "Kısık kalma süresi", "parlaklik", A, 0.5, 5.0, 0.1, "sn",
       "Kısılma sonrası ışığın kısık kaldığı süre."),
    _p("asagi_geri_sn", "Kısıktan dönüş süresi", "parlaklik", A, 1.0, 10.0, 0.5, "sn",
       "Kısık kaldıktan sonra normal parlaklığa dönüş süresi."),
    # ---------------- renk ----------------
    _p("doygunluk", "Renk canlılığı", "renk", B, 0.6, 1.0, 0.01, "",
       "Renklerin ne kadar canlı olacağı. Düşük değer pastele, yüksek değer saf renge yaklaşır; hiçbir zaman beyaza dönmez."),
    _p("renk_kayma", "Vuruşta renk kayması", "renk", B, 0.0, 60.0, 1.0, "°",
       "Her davul vuruşunda rengin kısa süre kayacağı miktar. 0 = renk vuruşla oynamaz."),
    _p("renk_ara_sn", "Renk değişim aralığı", "renk", B, 1.0, 30.0, 0.5, "sn",
       "Büyük patlamalarda rengin değişebilmesi için iki değişim arasında geçmesi gereken en kısa süre."),
    _p("uzun_sessiz_sn", "Sessizlik sonrası yeni renk", "renk", B, 0.5, 10.0, 0.1, "sn",
       "Bu kadar sessizlik ya da karanlıktan sonra müzik dönünce yepyeni bir renk seçilir."),
    _p("esik_renk_v", "Yeni renk parlaklık eşiği", "renk", B, 0.0, 0.5, 0.01, "",
       "Işık bu parlaklığın altına düşüp geri çıkınca zar atılır; tutarsa yeni renk gelir (aralık beklenmez)."),
    _p("esik_renk_olasilik", "Eşik sonrası yeni renk olasılığı", "renk", B, 0, 100, 5, "%",
       "Işık eşiğin altına düşüp geri çıktığında yeni renk gelme şansı. 0 = kapalı. Diğer renk kuralları aynen çalışır."),
    _p("esik_renk_sure_sn", "Eşik altında en az kalma", "renk", A, 0.0, 1.0, 0.05, "sn",
       "Işık eşiğin altında en az bu kadar kalmazsa zar atılmaz (vuruş arası kısa dipler sayılmaz)."),
    _a("palet_acik", "Palet", "renk", B,
       "Açıkken yeni renkler yalnız paletteki renklerden seçilir (en az 2 renk). Diğer renk kuralları aynen çalışır; canlılık Renk canlılığı ayarından gelir."),
    _a("palet_sira", "Palet sırası", "renk", B,
       "Kapalı = karışık (hemen önceki renk tekrar gelmez), Açık = eklediğin sırayla."),
    _p("palet_1", "Palet rengi 1", "renk", B, -1, 0xFFFFFF, 1, "", "Palet rengi (0xRRGGBB, -1 = boş).", tip="renk"),
    _p("palet_2", "Palet rengi 2", "renk", B, -1, 0xFFFFFF, 1, "", "Palet rengi (0xRRGGBB, -1 = boş).", tip="renk"),
    _p("palet_3", "Palet rengi 3", "renk", B, -1, 0xFFFFFF, 1, "", "Palet rengi (0xRRGGBB, -1 = boş).", tip="renk"),
    _p("palet_4", "Palet rengi 4", "renk", B, -1, 0xFFFFFF, 1, "", "Palet rengi (0xRRGGBB, -1 = boş).", tip="renk"),
    _p("palet_5", "Palet rengi 5", "renk", B, -1, 0xFFFFFF, 1, "", "Palet rengi (0xRRGGBB, -1 = boş).", tip="renk"),
    _p("palet_6", "Palet rengi 6", "renk", B, -1, 0xFFFFFF, 1, "", "Palet rengi (0xRRGGBB, -1 = boş).", tip="renk"),
    _p("palet_7", "Palet rengi 7", "renk", B, -1, 0xFFFFFF, 1, "", "Palet rengi (0xRRGGBB, -1 = boş).", tip="renk"),
    _p("palet_8", "Palet rengi 8", "renk", B, -1, 0xFFFFFF, 1, "", "Palet rengi (0xRRGGBB, -1 = boş).", tip="renk"),
    _a("renk_serbest", "Renk serbest", "renk", A,
       "Açıkken renk olaylarda rastgele ama birbirinden farklı seçilir. Kapalıyken renk tiz/bas dengesine göre akar (Spektrum gibi)."),
    _p("renk_en_az_fark", "Renkler arası en az fark", "renk", A, 0.0, 170.0, 5.0, "°",
       "Yeni rengin son üç renkten en az bu kadar uzak olması (renk çemberinde derece)."),
    _p("renk_tau_sn", "Renk geçiş yumuşaklığı", "renk", A, 0.05, 3.0, 0.05, "sn",
       "Renk değişimlerinin ne kadar yumuşak oturduğu (alçak geçiren zaman sabiti)."),
    _p("renk_kayma_sn", "Kayma sönümü", "renk", A, 0.05, 1.0, 0.05, "sn",
       "Vuruş renk kaymasının geri çekilme süresi."),
    _p("oran_tau_sn", "Bas-tiz renk yumuşaklığı", "renk", A, 0.1, 5.0, 0.1, "sn",
       "Yalnız 'Renk serbest' kapalıyken: bas-tiz dengesinin renge yansıma yumuşaklığı."),
    # ---------------- olaylar ----------------
    _a("kesilme_acik", "Kesilmede kararma", "olaylar", B,
       "Müzik aniden kesilince (ör. şarkıda sessiz boşluk) ışık söner."),
    _a("donus_acik", "Dönüşte patlama", "olaylar", B,
       "Kesilme ya da sakin bölüm sonrası müzik sert dönünce ışık tam parlaklıkta patlar ve renk değişir."),
    _a("vurgu_acik", "Vurguda kısa karartma", "olaylar", B,
       "Zil/crash gibi parlak vuruşlarda ışık çok kısa göz kırpar."),
    _p("kes_db", "Kesilme hassasiyeti", "olaylar", B, 6.0, 30.0, 0.5, "dB",
       "Ses, son 2 sn ortalamasının bu kadar altına inerse ışık kesilir. Küçük değer = daha kolay kesilir.",
       cizgi="seviye"),
    _p("don_db", "Dönüş hassasiyeti", "olaylar", B, 4.0, 20.0, 0.5, "dB",
       "Sesin dönüş sayılması için ani sıçraması gereken miktar. Küçük değer = daha kolay patlar.",
       cizgi="seviye"),
    _p("kes_once_db", "Kesilme öncesi tutarlılık", "olaylar", A, 2.0, 15.0, 0.5, "dB",
       "Kesilmeden hemen önce ses ortalamadan en fazla bu kadar altta olmalı (yavaş azalmalar kesilme sayılmaz)."),
    _p("kes_yeterli_db", "Kesilme için en az ses", "olaylar", A, -80.0, -30.0, 1.0, "dB",
       "Önceki 2 sn bundan sessizse kesilme sayılmaz.", cizgi="seviye"),
    _p("kes_ara_sn", "İki kesilme arası", "olaylar", A, 0.3, 5.0, 0.1, "sn", "Kesilmeler arası en kısa süre."),
    _p("kes_bekle_sn", "Kesilme bekleme süresi", "olaylar", A, 1.0, 6.0, 0.1, "sn",
       "Ses geri gelmezse ışığın yumuşakça açılmaya başlayacağı süre."),
    _p("kes_geri_db", "Sessizce açılma payı", "olaylar", A, 3.0, 15.0, 0.5, "dB",
       "Ses ortalamaya bu kadar yaklaşırsa kesilme biter."),
    _p("kes_acil_sn", "Açılış süresi", "olaylar", A, 0.1, 1.5, 0.05, "sn", "Kesilme sonrası ışığın açılma süresi."),
    _p("kes_ort_sn", "Karşılaştırma penceresi", "olaylar", A, 0.5, 4.0, 0.1, "sn",
       "Kesilme, bu kadar süren ses ortalamasına göre ölçülür."),
    _p("kes_gecikme_sn", "Pencere gecikmesi", "olaylar", A, 0.05, 0.4, 0.01, "sn",
       "Ortalama penceresinin şimdiki andan ne kadar geride bittiği."),
    _p("don_ara_sn", "İki dönüş arası (kesilmeli)", "olaylar", A, 1.0, 12.0, 0.5, "sn", "Kesilme sonrası dönüşler arası en kısa süre."),
    _p("don_ara_acik_sn", "İki dönüş arası (kesilmesiz)", "olaylar", A, 1.0, 12.0, 0.5, "sn",
       "Kesilme olmadan algılanan dönüşler arası en kısa süre."),
    _p("don_enerji", "Dönüş için en az enerji", "olaylar", A, 0.2, 1.0, 0.05, "",
       "Varış noktasının şarkının kendi aralığında en az bu kadar yüksek olması (0-1)."),
    _p("don_sur", "Sürekli artış uzunluğu", "olaylar", A, 3, 16, 1, "blok",
       "Hızlı artışın en az kaç blok (~11,6 ms) sürmesi gerektiği."),
    _p("don_sur_db", "Sürekli artış (gürlük)", "olaylar", A, 3.0, 16.0, 0.5, "dB", "Sürekli artışta gürlük sıçraması."),
    _p("don_sur_bas_db", "Sürekli artış (bas)", "olaylar", A, 1.0, 12.0, 0.5, "dB", "Sürekli artışta bas sıçraması."),
    _p("don_bas_db", "Hızlı dönüşte bas sıçraması", "olaylar", A, 2.0, 14.0, 0.5, "dB",
       "Kesilme sonrası hızlı dönüşte bas (sub+kick) sıçraması."),
    _p("don_uzun_sn", "Uzun karşılaştırma", "olaylar", A, 1.0, 6.0, 0.5, "sn",
       "Kesilmesiz dönüşte karşılaştırılan geçmiş süre."),
    _p("don_uzun_db", "Uzun karşılaştırma farkı", "olaylar", A, 3.0, 16.0, 0.5, "dB",
       "Kesilmesiz dönüşte uzun ortalamanın üstünde olması gereken miktar."),
    _p("don_sonum_sn", "Patlama sönümü", "olaylar", A, 0.05, 1.0, 0.01, "sn", "Dönüş patlamasının tabana inme süresi."),
    _p("don_pencere_sn", "Dönüş penceresi", "olaylar", A, 0.1, 0.6, 0.05, "sn", "Hızlı dönüşte karşılaştırılan kısa süre."),
    _p("anlik_kisa_sn", "Anlık patlama: kısa süre", "olaylar", A, 0.05, 0.6, 0.05, "sn", "Anlık patlamada kısa gürlük süresi."),
    _p("anlik_uzun_sn", "Anlık patlama: uzun süre", "olaylar", A, 1.0, 6.0, 0.5, "sn", "Anlık patlamada karşılaştırma süresi."),
    _p("anlik_gur_db", "Anlık patlama: gürlük", "olaylar", A, 1.0, 10.0, 0.5, "dB", "Anlık patlama için gürlük artışı."),
    _p("anlik_bas_db", "Anlık patlama: bas", "olaylar", A, 4.0, 20.0, 0.5, "dB", "Anlık patlama için bas artışı."),
    _p("anlik_ara_sn", "Anlık patlama arası", "olaylar", A, 1.0, 12.0, 0.5, "sn", "İki anlık patlama arası en kısa süre."),
    _p("isinma_sn", "Başlangıç ısınması", "olaylar", A, 0.0, 15.0, 0.5, "sn",
       "Müzik başlayınca olayların (kesilme, dönüş) devreye girmeden önce beklediği süre."),
    _p("vurgu_sn", "Vurgu süresi", "olaylar", A, 0.02, 0.3, 0.01, "sn", "Karartmanın süresi."),
    _p("vurgu_dusus", "Vurgu karartması", "olaylar", A, 0.05, 0.9, 0.05, "x", "Vurguda parlaklığın çarpıldığı oran (küçük = koyu)."),
    _p("vurgu_ara_sn", "İki vurgu arası", "olaylar", A, 3.0, 40.0, 1.0, "sn", "Vurgular arası en kısa süre."),
    _p("vurgu_isinma_sn", "Vurgu ısınması", "olaylar", A, 0.0, 30.0, 1.0, "sn", "Başlangıçta vurgunun kapalı kaldığı süre."),
    _p("vurgu_bas_max_db", "Vurgu bas sınırı", "olaylar", A, 1.0, 10.0, 0.5, "dB",
       "Bas bundan fazla sıçrarsa vurgu değil dönüş sayılır."),
    _p("vurgu_hat_oran", "Vurgu için tiz oranı", "olaylar", A, 2.0, 15.0, 0.5, "x",
       "Hi-hat/zil enerjisi ortalamasının kaç katı olunca vurgu sayılır."),
    _p("vurgu_db", "Vurgu gürlük artışı", "olaylar", A, 1.0, 10.0, 0.5, "dB", "Vurgu için gürlük artışı.", cizgi="seviye"),
    _p("uzun_sessiz_db", "Sessizlik eşiği", "olaylar", A, -80.0, -30.0, 1.0, "dB",
       "Kısa gürlük bunun altındaysa ortam sessiz sayılır (yeni renk için).", cizgi="seviye"),
    # ---------------- vokal ----------------
    _p("vokal_agirlik", "Vokal ağırlığı", "vokal", B, 0.0, 2.0, 0.05, "x",
       "Işığın ne kadarının şarkıcının sesini izlemesi gerektiği. 0 = vokal izlenmez, 1 = normal, 2 = en çok."),
    _p("donum_esik", "Duygu hassasiyeti", "vokal", B, 0.04, 0.3, 0.01, "",
       "Vokal yoğunluğu değişince renk değişir. Küçük değer = duygu değişimine daha çabuk tepki."),
    _a("duygu_acik", "Duygu tepkisi", "vokal", A,
       "Açıkken vokal duygusu değişince renk değişir (yükselirken parlar, düşerken kısılır)."),
    _p("vt_w", "Vokal payı", "vokal", A, 0.1, 1.0, 0.05, "x", "Vokal ağırlığı 1 iken parlaklığın vokalden gelen payı."),
    _p("vt_ust", "Vokal payı üst sınırı", "vokal", A, 0.5, 1.0, 0.05, "x", "Vokal payı bundan fazla olamaz."),
    _p("vt_taban", "Vokal taban parlaklığı", "vokal", A, 0.0, 0.5, 0.05, "", "Vokal varken en az parlaklık katkısı."),
    _p("vt_guc", "Vokal ses gücü etkisi", "vokal", A, 0.0, 1.0, 0.05, "", "Parlaklığın vokal gürlüğünü izleme miktarı."),
    _p("vt_perde", "Vokal perde etkisi", "vokal", A, 0.0, 0.5, 0.05, "", "Parlaklığın vokal tizliğini izleme miktarı."),
    _p("vt_atak_vurus", "Vokal giriş hızı", "vokal", A, 0.25, 4.0, 0.25, "vuruş", "Vokal başlayınca takibin ne kadar vuruşta oturduğu."),
    _p("vt_sonum_vurus", "Vokal çıkış hızı", "vokal", A, 0.25, 6.0, 0.25, "vuruş", "Vokal bitince takibin ne kadar vuruşta çözüldüğü."),
    _p("vt_perde_tau", "Perde yumuşaklığı", "vokal", A, 0.02, 1.0, 0.01, "sn", "Perde izlemenin yumuşatma süresi."),
    _p("vt_aralik_tau", "Perde aralığı unutma", "vokal", A, 5.0, 90.0, 5.0, "sn", "Şarkıcının perde aralığının unutulma süresi."),
    _p("yog_tau_sn", "Yoğunluk yumuşaklığı", "vokal", A, 0.3, 6.0, 0.1, "sn", "Vokal yoğunluğu ölçümünün yumuşatılması."),
    _p("donum_ara_sn", "İki duygu dönüşü arası", "vokal", A, 2.0, 20.0, 0.5, "sn", "Duygu değişimleri arası en kısa süre."),
    _p("yukari_sonum_sn", "Yükselişte patlama sönümü", "vokal", A, 0.05, 1.0, 0.05, "sn", "Duygu yükselişindeki parlamanın sönümü."),
    # ---------------- vurus ----------------
    _p("hassasiyet", "Vuruş hassasiyeti", "vurus", B, 1, 100, 1, "", "Davul vuruşlarını ne kadar kolay yakaladığı. Yüksek değer = daha çok vuruş.",
       varsayilan=50),
    _p("vt_nabiz_pay", "Vuruş nabzı payı", "vurus", B, 0.0, 1.0, 0.05, "x",
       "Vokal varken davul vuruşunun ışıkta ne kadar hissedileceği (0 = vokal varken nabız yok)."),
    _p("vurus_sonum_sn", "Vuruş sönümü", "vurus", A, 0.03, 0.5, 0.01, "sn", "Davul vuruşundan sonra parlamanın sönme süresi."),
    _p("nabiz_alt", "Vuruş en az parlaklığı", "vurus", A, 0.0, 1.0, 0.05, "x",
       "Sessiz kısımlarda bile vuruşun ışığa katacağı en küçük parlaklık payı."),
    # ---------------- gonderim ----------------
    _p("send_hz", "Gönderim hızı", "gonderim", B, 4, 20, 1, "Hz",
       "Şeride saniyede kaç renk gönderileceği. Yüksek = akıcı ama şerit/ağ zorlanabilir.", varsayilan=16),
]

_INDEKS = {p["id"]: p for p in PARAMETRELER}
MOTOR_IDLERI = tuple(p["id"] for p in PARAMETRELER if p["id"] not in MOTOR_DISI)

# ---- sablonlar: yalniz varsayilandan farklar ----
SABLONLAR = {
    "dionysos": {},
    # Spektrum gibi: olaylar ve vokal izleme kapali, renk bas-tiz oranindan akar
    "spektrum_gibi": {
        "kesilme_acik": 0, "donus_acik": 0, "vurgu_acik": 0, "duygu_acik": 0,
        "vokal_agirlik": 0.0, "renk_serbest": 0, "oran_tau_sn": 0.4, "renk_tau_sn": 0.2, "renk_kayma": 12.0,
    },
}


def dogrula(params):
    """{id: deger} -> yalniz bilinen kimlikler, sinirlara kisilmis, anahtarlar 0/1. Gecersiz degerler atlanir."""
    out = {}
    if not isinstance(params, dict):
        return out
    for k, v in params.items():
        p = _INDEKS.get(k) if isinstance(k, str) else None
        if p is None:
            continue
        if isinstance(v, str) or v is None or isinstance(v, (list, dict, tuple)):
            continue
        try:
            d = float(v)
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(d):
            continue
        if p["tip"] == "anahtar":
            out[k] = 1 if d else 0
            continue
        d = max(p["min"], min(p["max"], d))
        if isinstance(p["varsayilan"], int):
            d = int(round(d))
        out[k] = d
    return out


def sablon_adi(sablon):
    return sablon if sablon in SABLONLAR else "dionysos"


def tam_params(sablon, params=None):
    """Motor parametrelerinin tam seti: varsayilan + sablon + params. hassasiyet/send_hz yalniz
    sablonda ya da params'ta acikca varsa eklenir (yoksa kullanicinin mevcut ayari korunur)."""
    tam = {i: _INDEKS[i]["varsayilan"] for i in MOTOR_IDLERI}
    tam.update(dogrula(SABLONLAR[sablon_adi(sablon)]))
    ek = dogrula(params or {})
    tam.update(ek)
    for i in MOTOR_DISI:
        if i in ek:
            tam[i] = ek[i]
    return tam


def motor_params(tam):
    return {k: v for k, v in tam.items() if k not in MOTOR_DISI}


def varsayilan_motor():
    return {i: _INDEKS[i]["varsayilan"] for i in MOTOR_IDLERI}


def parametre_listesi():
    return copy.deepcopy(PARAMETRELER)


def cizgiler(p, ort_db, spek_dc_db):
    """Ekolayzir esik cizgileri. p: motor parametreleri (motor.p), ort_db: kesilme karsilastirma ortalamasi
    (yoksa None). Gurluk cizgileri 'seviye' olcegindedir (toplam guc dB); spektrum cizgileri bin basina
    olcege cevrilmistir (spek_dc_db: toplam gucten bin basina gecen dB, ~ -10*log10(bin sayisi))."""
    sev, spk = [], []
    if ort_db is not None:
        sev.append({"id": "kes_db", "deger_db": round(ort_db - p["kes_db"], 1), "etiket": "Kesilme çizgisi"})
        sev.append({"id": "don_db", "deger_db": round(ort_db + p["don_db"], 1), "etiket": "Dönüş çizgisi"})
        sev.append({"id": "vurgu_db", "deger_db": round(ort_db + p["vurgu_db"], 1), "etiket": "Vurgu çizgisi"})
    sev.append({"id": "kes_yeterli_db", "deger_db": round(p["kes_yeterli_db"], 1), "etiket": "Kesilme için en az ses"})
    sev.append({"id": "uzun_sessiz_db", "deger_db": round(p["uzun_sessiz_db"], 1), "etiket": "Sessizlik eşiği"})
    spk.append({"id": "uzun_sessiz_db", "deger_db": round(p["uzun_sessiz_db"] + spek_dc_db, 1),
                "etiket": "Sessizlik eşiği (yaklaşık)"})
    return {"seviye": sev, "spektrum": spk}


# ---------------------------------------------------------------- mod deposu
def _simdi():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _temiz_ad(ad):
    if not isinstance(ad, str):
        return ""
    ad = "".join(c for c in ad if c.isprintable()).strip()
    return ad[:AD_MAX]


def _dosya_adi(ad):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", ad).strip(" .").replace(" ", "_")
    return (s or "mod")[:AD_MAX]


class ModDeposu:
    """modlar.json: mod listesi. Bellekte tutulur; her degisiklik atomik (gecici dosya + os.replace) yazilir.
    Bozuk dosya acilista bos liste sayilir (cokme yok); ilk kayitta bozuk dosya .bozuk olarak yana alinir."""

    def __init__(self, yol, disari_klasoru):
        self._yol = yol
        self._disari = disari_klasoru
        self._lock = threading.RLock()
        self._bozuk = False
        self._modlar = self._yukle()

    def _yukle(self):
        try:
            with open(self._yol, encoding="utf-8") as f:
                veri = json.load(f)
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            self._bozuk = True
            return []
        if isinstance(veri, dict):
            veri = veri.get("modlar")
        if not isinstance(veri, list):
            self._bozuk = True
            return []
        out, gorulen = [], set()
        for m in veri:
            if not isinstance(m, dict):
                continue
            i, ad = m.get("id"), _temiz_ad(m.get("ad"))
            if not isinstance(i, str) or not ID_RE.match(i) or not ad or i in gorulen:
                continue
            gorulen.add(i)
            out.append({"id": i, "ad": ad, "sablon": sablon_adi(m.get("sablon")),
                        "params": dogrula(m.get("params")),
                        "olusturma": str(m.get("olusturma") or ""), "guncelleme": str(m.get("guncelleme") or "")})
        return out

    def _yaz(self, yeni):
        """Atomik yazim; basarisizsa OSError."""
        if self._bozuk and os.path.exists(self._yol):
            try:
                os.replace(self._yol, self._yol + ".bozuk")
            except OSError:
                pass
        self._bozuk = False
        tmp = self._yol + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(yeni, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._yol)

    def liste(self):
        with self._lock:
            return [{"id": m["id"], "ad": m["ad"], "sablon": m["sablon"]} for m in self._modlar]

    def getir(self, mod_id):
        with self._lock:
            for m in self._modlar:
                if m["id"] == mod_id:
                    return copy.deepcopy(m)
        return None

    def kaydet(self, mod_id, ad, sablon, params):
        """Kaydedilen modu doner; gecersiz girdide {"hata": ...}. mod_id None ya da bilinmiyorsa yeni mod."""
        ad = _temiz_ad(ad)
        if not ad:
            return {"hata": "Mod adı boş olamaz."}
        with self._lock:
            mevcut = None
            for m in self._modlar:
                if m["id"] == mod_id:
                    mevcut = m
            simdi = _simdi()
            yeni = {"id": mevcut["id"] if mevcut else uuid.uuid4().hex[:8], "ad": ad, "sablon": sablon_adi(sablon),
                    "params": dogrula(params),
                    "olusturma": mevcut["olusturma"] if mevcut else simdi, "guncelleme": simdi}
            liste = [yeni if m is mevcut else m for m in self._modlar]
            if mevcut is None:
                liste.append(yeni)
            try:
                self._yaz(liste)
            except OSError as e:
                return {"hata": f"modlar.json yazılamadı ({type(e).__name__})"}
            self._modlar = liste
            return copy.deepcopy(yeni)

    def sil(self, mod_id):
        with self._lock:
            kalan = [m for m in self._modlar if m["id"] != mod_id]
            if len(kalan) == len(self._modlar):
                return False
            try:
                self._yaz(kalan)
            except OSError:
                return False
            self._modlar = kalan
            return True

    def disa_aktar(self, mod_id):
        m = self.getir(mod_id)
        if m is None:
            return {"hata": "Mod bulunamadı."}
        try:
            os.makedirs(self._disari, exist_ok=True)
            yol = os.path.join(self._disari, _dosya_adi(m["ad"]) + ".json")
            kok = os.path.abspath(self._disari)
            if os.path.dirname(os.path.abspath(yol)) != kok:
                return {"hata": "Geçersiz dosya adı."}
            tmp = yol + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(m, f, ensure_ascii=False, indent=2)
            os.replace(tmp, yol)
        except OSError as e:
            return {"hata": f"Dışa aktarılamadı ({type(e).__name__})"}
        return {"yol": yol}

    def ice_aktar(self, yol):
        """Dışa aktarılmış .json dosyasından yeni mod olarak içe alır (id yeniden üretilir)."""
        try:
            if not isinstance(yol, str) or not yol.lower().endswith(".json") or not os.path.isfile(yol):
                return {"hata": "Geçerli bir .json dosyası değil."}
            if os.path.getsize(yol) > DOSYA_MAX:
                return {"hata": "Dosya çok büyük."}
            with open(yol, encoding="utf-8") as f:
                veri = json.load(f)
        except (OSError, ValueError):
            return {"hata": "Dosya okunamadı."}
        if not isinstance(veri, dict):
            return {"hata": "Mod dosyası geçersiz."}
        return self.kaydet(None, veri.get("ad"), veri.get("sablon"), veri.get("params"))
