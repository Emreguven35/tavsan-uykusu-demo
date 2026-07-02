"""
Merkezi LLM yapılandırması.

Model adları TEK yerden yönetilir — değiştirmek için sadece aşağıdaki satırları güncelle.
plan_generator.py PLAN_MODEL'i, chatbot.py CHATBOT_MODEL'i içe aktarır.

Plan üretimi (yazar rolü) yüksek kaliteli Sonnet'te kalır; chatbot/RAG (kısa,
sık, düşük-riskli cevaplar) daha ucuz ve hızlı Haiku'ya alındı.
"""

# Plan üretici modeli — DEĞİŞMEDİ (yüksek kaliteli yazar).
PLAN_MODEL = "claude-sonnet-4-6"

# Chatbot / RAG modeli — Haiku 4.5 (ucuz + hızlı; kısa sık cevaplar).
CHATBOT_MODEL = "claude-haiku-4-5"

# Geriye dönük uyumluluk: build_embeddings.py doc2query üretimi bu adı kullanır
# (build-time içerik üretimi — yazar modeliyle aynı kalsın).
MODEL_NAME = PLAN_MODEL
