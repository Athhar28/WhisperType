"""Convert Devanagari Hindi to casual Roman "Hinglish" (the way people type it).

    to_hinglish("नमस्ते, आज मैं बहुत खुश हूँ।")  ->  "Namaste, aaj main bahut khush hoon."

Rule based: a phoneme pass, Hindi schwa deletion, casual vowel spelling, plus a
small dictionary for very common words and English loanwords.
"""
import re
import unicodedata

VIRAMA, NUKTA, ANUSVARA, CHANDRA, VISARGA = "्", "़", "ं", "ँ", "ः"

CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "f", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v",
    "श": "sh", "ष": "sh", "स": "s", "ह": "h", "ळ": "l",
}
NUKTA_CONS = {"क": "q", "ख": "kh", "ग": "g", "ज": "z", "ड": "d", "ढ": "dh", "फ": "f"}

# Long vowels are kept symbolic (A, I, U) and spelled by position later.
VOWELS = {"अ": "a", "आ": "A", "इ": "i", "ई": "I", "उ": "u", "ऊ": "U", "ऋ": "ri",
          "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऑ": "o", "ऍ": "e"}
MATRAS = {"ा": "A", "ि": "i", "ी": "I", "ु": "u", "ू": "U", "ृ": "ri",
          "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o", "ॅ": "e"}
DIGITS = {chr(0x0966 + i): str(i) for i in range(10)}

# Everyday spellings the rules would get slightly wrong.
COMMON = {
    "में": "mein", "नहीं": "nahi", "हूँ": "hoon", "हूं": "hoon", "हाँ": "haan", "हां": "haan",
    "क्यों": "kyun", "क्यूँ": "kyun", "क्योंकि": "kyunki", "यह": "yeh", "वह": "woh", "वो": "wo",
    "हम": "hum", "कुछ": "kuch", "अच्छा": "accha", "अच्छी": "acchi", "अच्छे": "acche",
    "चाहिए": "chahiye", "चाहिये": "chahiye", "कृपया": "kripya", "धन्यवाद": "dhanyavaad",
    "ज़्यादा": "zyada", "ज्यादा": "zyada", "वाला": "wala", "वाली": "wali", "वाले": "wale",
    "यहाँ": "yahan", "यहां": "yahan", "वहाँ": "wahan", "वहां": "wahan", "कहाँ": "kahan", "कहां": "kahan",
    "जहाँ": "jahan", "जहां": "jahan", "पहले": "pehle", "पहला": "pehla", "पहली": "pehli",
    "कहना": "kehna", "कहता": "kehta", "कहती": "kehti", "कहते": "kehte", "कह": "keh",
    "रहना": "rehna", "रहता": "rehta", "रहती": "rehti", "रहते": "rehte", "रह": "reh",
    "बहन": "behen", "शहर": "sheher", "मेहनत": "mehnat", "भाई": "bhai", "माँ": "maa", "मां": "maa",
    "ज़िंदगी": "zindagi", "जिंदगी": "zindagi", "ज़िन्दगी": "zindagi", "सकता": "sakta", "सकती": "sakti",
    "सकते": "sakte", "बिल्कुल": "bilkul", "बिलकुल": "bilkul", "मतलब": "matlab", "वक़्त": "waqt", "वक्त": "waqt",
    "साथ": "saath", "बाद": "baad", "पास": "paas", "हमारा": "hamara", "हमारी": "hamari", "हमारे": "hamare",
    "तुम्हारा": "tumhara", "तुम्हारी": "tumhari", "तुम्हारे": "tumhare", "ज़रूरी": "zaroori", "जरूरी": "zaroori",
    "ज़रूर": "zaroor", "जरूर": "zaroor", "दोनों": "dono", "लोगों": "logon", "बातें": "baatein",
    "मैंने": "maine", "चाय": "chai", "तुम्हें": "tumhe", "उन्हें": "unhe", "इन्हें": "inhe", "हमें": "humein",
    "फिर": "phir", "फल": "phal", "फूल": "phool", "उसे": "use", "मुझे": "mujhe", "तुझे": "tujhe",
    "व": "va", "न": "na", "इसलिए": "isliye", "दुनिया": "duniya", "सिर्फ़": "sirf", "सिर्फ": "sirf",
}

# English words Whisper writes in Devanagari when you speak Hinglish.
LOANWORDS = {
    "टेस्ट": "test", "मीटिंग": "meeting", "ऐप": "app", "एप": "app", "फोन": "phone", "फ़ोन": "phone",
    "मोबाइल": "mobile", "वीडियो": "video", "ऑफिस": "office", "ऑफ़िस": "office", "कंप्यूटर": "computer",
    "लैपटॉप": "laptop", "इंटरनेट": "internet", "ईमेल": "email", "मैसेज": "message", "मेसेज": "message",
    "फाइल": "file", "फ़ाइल": "file", "डॉक्टर": "doctor", "स्कूल": "school", "कॉलेज": "college",
    "टाइम": "time", "प्लीज़": "please", "प्लीज": "please", "सॉरी": "sorry", "थैंक्यू": "thank you",
    "थैंक": "thank", "यू": "you", "ओके": "ok", "हेलो": "hello", "हैलो": "hello", "बाय": "bye",
    "यूट्यूब": "YouTube", "चैनल": "channel", "सब्सक्राइब": "subscribe", "लाइक": "like", "शेयर": "share",
    "कमेंट": "comment", "पार्ट": "part", "सीरीज़": "series", "सीरीज": "series", "डिसिप्लिन": "discipline",
    "गूगल": "Google", "ऑनलाइन": "online", "प्रोजेक्ट": "project", "रिपोर्ट": "report", "टीम": "team",
    "बैंक": "bank", "कॉल": "call", "नंबर": "number", "प्रॉब्लम": "problem", "सिस्टम": "system",
    "सॉफ्टवेयर": "software", "वर्क": "work", "गुड": "good", "मॉर्निंग": "morning", "नाइट": "night",
    "हैप्पी": "happy", "बर्थडे": "birthday", "लाइफ": "life", "लाइफ़": "life", "फोकस": "focus", "फ़ोकस": "focus",
    "गोल": "goal", "प्लान": "plan", "स्टार्ट": "start", "स्टॉप": "stop", "ट्राई": "try", "चेक": "check",
    "सेंड": "send", "डाउनलोड": "download", "अपलोड": "upload", "पासवर्ड": "password", "लिंक": "link",
    "फोटो": "photo", "फ़ोटो": "photo", "कैमरा": "camera", "गेम": "game", "मूवी": "movie", "सॉन्ग": "song",
    "फ्रेंड": "friend", "फ़्रेंड": "friend", "फैमिली": "family", "फ़ैमिली": "family", "बॉस": "boss",
    "मैनेजर": "manager", "कस्टमर": "customer", "ऑर्डर": "order", "पेमेंट": "payment", "मनी": "money",
    "बिज़नेस": "business", "बिजनेस": "business", "मार्केट": "market", "क्लास": "class", "होमवर्क": "homework",
    "एग्जाम": "exam", "रिजल्ट": "result", "रिज़ल्ट": "result", "फ्राइडे": "Friday", "मंडे": "Monday",
    "संडे": "Sunday", "वीक": "week", "वीकेंड": "weekend", "मिनट": "minute", "सेकंड": "second",
}

_DEV_RUN = re.compile(r"[ऀ-ॿ]+")


def _phonemes(word):
    """word -> list of [text, kind]; kind is C consonant, V vowel, S schwa, N nasal."""
    out, i, n = [], 0, len(word)
    while i < n:
        ch = word[i]
        if ch in CONS:
            rom = CONS[ch]
            if ch == "छ" and i > 0:              # "chhota" but "pichle", "accha"
                rom = "ch"
            if i + 1 < n and word[i + 1] == NUKTA:
                rom = NUKTA_CONS.get(ch, rom)
                i += 1
            if ch == "ज" and word[i + 1:i + 3] == VIRAMA + "ञ":   # ज्ञ
                rom = "gy"
                i += 2
            out.append([rom, "C"])
            nxt = word[i + 1] if i + 1 < n else ""
            if nxt == VIRAMA:
                i += 2
            elif nxt in MATRAS:
                out.append([MATRAS[nxt], "V"])
                i += 2
            else:
                out.append(["a", "S"])
                i += 1
            continue
        if ch in VOWELS:
            out.append([VOWELS[ch], "V"])
        elif ch in MATRAS:                       # stray matra
            out.append([MATRAS[ch], "V"])
        elif ch in (ANUSVARA, CHANDRA):
            out.append(["n", "N"])
        elif ch == VISARGA:
            out.append(["h", "C"])
        elif ch in DIGITS:
            out.append([DIGITS[ch], "D"])
        elif ch == "।" or ch == "॥":
            out.append([".", "D"])
        i += 1
    return out


def _delete_schwas(ph):
    def vowel(k):
        return 0 <= k < len(ph) and ph[k][1] in ("V", "S")

    def cons(k):
        return 0 <= k < len(ph) and ph[k][1] == "C"

    # Word-final schwa is silent: "कल" -> kal. Kept for one-syllable words and
    # for endings like "मित्र" (mitra), "सत्य" (satya).
    last = len(ph) - 1
    if last >= 2 and ph[last][1] == "S":
        other_vowel = any(p[1] in ("V", "S") for p in ph[:last])
        cluster_glide = cons(last - 2) and ph[last - 1][0] in ("y", "r", "v")
        if other_vowel and not cluster_glide:
            del ph[last]
    # Medial schwa is silent between vowel-consonant and consonant-vowel:
    # "करना" -> karna, "मतलब" -> matlab. Right to left, as in spoken Hindi.
    k = len(ph) - 2
    while k >= 2:
        if ph[k][1] == "S" and cons(k - 1) and vowel(k - 2) and cons(k + 1) and vowel(k + 2):
            del ph[k]
        k -= 1
    return ph


def _spell(ph):
    vowel_positions = [k for k, p in enumerate(ph) if p[1] in ("V", "S")]
    first_vowel = vowel_positions[0] if vowel_positions else -1
    out = []
    for k, (text, kind) in enumerate(ph):
        rest = ph[k + 1:]
        final = not rest
        before_final_nasal = len(rest) == 1 and rest[0][1] == "N"
        if kind == "V" and text in ("A", "I", "U"):
            if text == "A":
                text = "aa" if (before_final_nasal or (not final and k == first_vowel)) else "a"
            elif text == "I":
                text = "i" if (final or before_final_nasal) else "ee"
            else:
                text = "oo" if (before_final_nasal or not final) else "u"
        elif kind == "N":
            nxt = rest[0][0] if rest else ""
            text = "m" if nxt[:1] in ("p", "b", "m") else "n"
        # glide between vowels: लिए -> liye, गई -> gayi
        if kind in ("V", "S") and out and k > 0 and ph[k - 1][1] in ("V", "S"):
            prev = ph[k - 1][0]
            cur = ph[k][0]
            if ((prev in ("i", "I") and cur == "e") or (prev == "a" and cur in ("I", "i", "e"))
                    or (prev == "A" and cur in ("I", "e"))):
                out.append("y")
        out.append(text)
    return "".join(out)


def word_to_hinglish(word, extra=None):
    word = unicodedata.normalize("NFC", word)
    for table in (extra or {}, LOANWORDS, COMMON):
        if word in table:
            return table[word]
    return _spell(_delete_schwas(_phonemes(word)))


def to_hinglish(text, extra=None):
    text = _DEV_RUN.sub(lambda m: word_to_hinglish(m.group(0), extra), text)
    text = re.sub(r"\s+([,.?!])", r"\1", text)
    # Capitalise sentence starts.
    return re.sub(r"(^|[.?!]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
