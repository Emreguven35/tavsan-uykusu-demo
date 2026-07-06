/* ============================================================================
   test_avatar_audio.mjs — avatar_audio.js PCM/format birim testleri (Node).

   Çalıştırma:  node test_avatar_audio.mjs

   NOT: MP3 → Float32 çözümü tarayıcıda Web Audio (OfflineAudioContext) ile yapılır
   ve Node'da çalıştırılamaz; o aşama canlı testte doğrulanır. Bu birim testi, HATANIN
   BULUNDUĞU deterministik aşamayı doğrular: Float32 → PCM16 LE → BASE64.

   Doğrulanan format sözleşmesi (repeatAudio):
     BASE64( ham 16-bit signed little-endian PCM, 24 kHz, mono )
   ========================================================================== */
import {
  floatTo16BitPCM, bytesToBinaryString, bytesToBase64, pcmToSpeakString,
  PCM_SAMPLE_RATE, PCM_BITS, PCM_CHANNELS, BYTES_PER_SAMPLE,
} from "./avatar_audio.js";

const results = [];
const check = (name, cond, detail = "") => results.push([name, !!cond, detail]);
const bytesEq = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);

// --- 1) Format sabitleri: 24kHz / 16-bit / mono ------------------------------
check("1) format sabitleri 24kHz/16-bit/mono",
  PCM_SAMPLE_RATE === 24000 && PCM_BITS === 16 && PCM_CHANNELS === 1 && BYTES_PER_SAMPLE === 2,
  `rate=${PCM_SAMPLE_RATE} bits=${PCM_BITS} ch=${PCM_CHANNELS} bps=${BYTES_PER_SAMPLE}`);

// --- 2) Bit depth / byte tutarlılığı: n örnek → n*2 bayt ----------------------
const n = 1000;
const ramp = Float32Array.from({ length: n }, (_, i) => (i / n) * 2 - 1); // -1..1
const rampBytes = floatTo16BitPCM(ramp);
check("2) n örnek → n*2 bayt (16-bit)",
  rampBytes.length === n * 2, `len=${rampBytes.length} beklenen=${n * 2}`);

// --- 3) Int16 LE değer doğruluğu (bilinen örnekler) --------------------------
// 1.0→0x7fff→[ff,7f], -1.0→-0x8000→[00,80], 0→[00,00], 0.5→16383→0x3fff→[ff,3f]
const known = floatTo16BitPCM(Float32Array.from([1.0, -1.0, 0.0, 0.5]));
check("3) Int16 little-endian değerleri doğru",
  bytesEq(Array.from(known), [0xff, 0x7f, 0x00, 0x80, 0x00, 0x00, 0xff, 0x3f]),
  Array.from(known).map((b) => b.toString(16).padStart(2, "0")).join(" "));

// --- 4) Clamp: |x|>1 aralık dışına taşmaz ------------------------------------
const clamped = floatTo16BitPCM(Float32Array.from([2.0, -2.0]));
check("4) clamp ±1 (taşma yok)",
  bytesEq(Array.from(clamped), [0xff, 0x7f, 0x00, 0x80]),
  Array.from(clamped).map((b) => b.toString(16).padStart(2, "0")).join(" "));

// --- 5) 1 saniye @24kHz mono → 48000 bayt (SDK chunk matematiğiyle tutarlı) ---
const oneSec = new Float32Array(PCM_SAMPLE_RATE);            // 24000 örnek = 1 sn
const oneSecBytes = floatTo16BitPCM(oneSec);
check("5) 1 sn @24kHz → 48000 bayt",
  oneSecBytes.length === 48000, `len=${oneSecBytes.length}`);

// --- 6) base64 round-trip: decode(base64) === ham baytlar --------------------
const sine = Float32Array.from({ length: 24000 }, (_, i) => Math.sin(i / 20) * 0.8); // "örnek ses"
const sineBytes = floatTo16BitPCM(sine);
const b64 = bytesToBase64(sineBytes);
const decoded = new Uint8Array(Buffer.from(b64, "base64"));
check("6) base64 çözülünce ham baytlara eşit",
  bytesEq(Array.from(decoded), Array.from(sineBytes)),
  `bytes=${sineBytes.length} decoded=${decoded.length}`);

// --- 7) base64 uzunluğu = ceil(n/3)*4 ----------------------------------------
const expB64Len = Math.ceil(sineBytes.length / 3) * 4;
check("7) base64 uzunluğu ceil(n/3)*4",
  b64.length === expB64Len, `len=${b64.length} beklenen=${expB64Len}`);

// --- 8) KÖK NEDEN GUARD: base64 ASCII-güvenli; binary DEĞİL ------------------
// Eski hata: ham "binary string" WebSocket/JSON üzerinden UTF-8'e kaçıp bozuluyordu.
// base64 yalnız [A-Za-z0-9+/=] içerir (hepsi <0x80). binary ise 0x80+ char içerir.
const b64Ascii = /^[A-Za-z0-9+/]*={0,2}$/.test(b64);
const binStr = bytesToBinaryString(sineBytes);
let binHasHighChar = false;
for (let i = 0; i < binStr.length; i++) { if (binStr.charCodeAt(i) > 0x7f) { binHasHighChar = true; break; } }
check("8) base64 ASCII-güvenli & binary değil (kök neden)",
  b64Ascii && binHasHighChar,
  `b64Ascii=${b64Ascii} binaryHasHighChar=${binHasHighChar}`);

// --- 9) pcmToSpeakString varsayılanı base64; "binary" farklı ------------------
const def = pcmToSpeakString(sine);
const bin = pcmToSpeakString(sine, "binary");
check("9) pcmToSpeakString varsayılan=base64, binary != base64",
  def === b64 && bin === binStr && def !== bin,
  `def==b64:${def === b64} bin==binStr:${bin === binStr}`);

// --- özet --------------------------------------------------------------------
console.log("\n" + "=".repeat(74));
console.log("AVATAR AUDIO (PCM/format) TEST SONUÇLARI");
console.log("=".repeat(74));
let passed = 0;
for (const [name, ok, detail] of results) {
  if (ok) passed++;
  console.log(`[${ok ? "PASS" : "FAIL"}] ${name}\n       ${detail}`);
}
console.log("-".repeat(74));
console.log(`TOPLAM: ${passed}/${results.length} gecti`);
console.log("=".repeat(74));
process.exit(passed === results.length ? 0 : 1);
