// Kopeeri see fail nimega config.h (samasse kausta) ja täida oma andmetega.
// config.h on .gitignore-is - WiFi parool ei satu git'i.
#pragma once

#define WIFI_SSID      "minu-wifi"        // ainult 2.4 GHz võrk
#define WIFI_PASSWORD  "minu-parool"

// Masin, kus jookseb `python app.py --host 0.0.0.0` (app.py prindib käivitamisel õige IP).
// IP-aadress on kõige kindlam; "nimi.local" proovitakse lahendada mDNS-iga (nt "raspberrypi.local").
#define SERVER_HOST    "192.168.1.10"
#define SERVER_PORT    5000

// Valikulised seaded (vaikeväärtused collector.ino alguses):
// #define LED_BRIGHTNESS 24                  // 0..255 olekutulede heledus, 0 = LED-id kustu
// #define LED_ACTIVE_LOW 0                   // 1 (vaikimisi) = tagurpidi plaat: LOW = põleb (voicestripi
//                                            //   MOSFET-plaat, nagu Pi inference.py). 0 = HIGH = põleb.
// #define I2S_MIC_SLOT   I2S_STD_SLOT_RIGHT  // kui tase on alati 0 (L/R pin on VDD-s, mitte GND-s)
// #define GAIN_SHIFT     16                  // 16 = sama tase mis Pi I2S salvestustel; 14 = 4x valjem
