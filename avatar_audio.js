/* ============================================================================
   avatar_audio.js — LiveAvatar LITE ses format yardımcıları (tarayıcı + Node).

   KESİN SÖZLEŞME (SDK demo'sundan doğrulandı):
   @heygen/liveavatar-web-sdk demosu (apps/demo/.../useTextChat.ts) ElevenLabs'i
   `output_format=pcm_24000` ile çağırır, dönen `audio_base64` alanını DOĞRUDAN
   `session.repeatAudio(audio)`'ya verir. Yani:

     repeatAudio() girdisi = BASE64( ham PCM, 16-bit signed little-endian, 24 kHz, mono )

   Bizim İlayda sesimiz MP3'tür → tarayıcıda OfflineAudioContext ile 24 kHz mono
   Float32'ye indirilir → burada Int16 LE bayta → BASE64'e çevrilir.

   ÖNCEKİ HATA: base64 yerine ham "binary string" gönderiliyordu. JSON/WebSocket
   üzerinden 0x80–0xFF baytları UTF-8'e kaçtığı için sunucunun base64 çözümü
   bozuluyor, avatar konuşmuyordu (fallback devreye giriyordu). Base64 bunu çözer.
   ========================================================================== */

export const PCM_SAMPLE_RATE = 24000;   // Hz
export const PCM_BITS = 16;             // signed little-endian
export const PCM_CHANNELS = 1;          // mono
export const BYTES_PER_SAMPLE = (PCM_BITS / 8) * PCM_CHANNELS;  // 2

/** Float32 [-1,1] örnekleri → Int16 LE bayt dizisi (Uint8Array, len = n*2). */
export function floatTo16BitPCM(float32) {
  const out = new Uint8Array(float32.length * 2);
  let o = 0;
  for (let i = 0; i < float32.length; i++) {
    let s = float32[i];
    if (s > 1) s = 1; else if (s < -1) s = -1;      // clamp
    s = s < 0 ? s * 0x8000 : s * 0x7fff;            // asimetrik ölçek (int16 aralığı)
    const v = s | 0;                                 // truncate → int
    out[o++] = v & 0xff;                             // düşük bayt (little-endian)
    out[o++] = (v >> 8) & 0xff;                      // yüksek bayt
  }
  return out;
}

/** Bayt dizisi → latin1 "binary string" (1 char = 1 bayt). Yığın taşmasına karşı parçalı. */
export function bytesToBinaryString(bytes) {
  let s = "";
  const CH = 0x8000;
  for (let i = 0; i < bytes.length; i += CH) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
  }
  return s;
}

/** Bayt dizisi → base64 (tarayıcıda btoa, Node'da Buffer). */
export function bytesToBase64(bytes) {
  if (typeof btoa === "function") {
    return btoa(bytesToBinaryString(bytes));
  }
  // Node fallback
  return Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength).toString("base64");
}

/**
 * repeatAudio()'ya verilecek dizgeyi üret.
 * @param {Float32Array} float32  24 kHz mono örnekler
 * @param {"base64"|"binary"} encoding  "base64" = DOĞRU (varsayılan);
 *        "binary" = eski/yanlış davranış (yalnız A/B karşılaştırma için).
 */
export function pcmToSpeakString(float32, encoding = "base64") {
  const bytes = floatTo16BitPCM(float32);
  return encoding === "binary" ? bytesToBinaryString(bytes) : bytesToBase64(bytes);
}
