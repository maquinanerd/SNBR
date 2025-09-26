# migrate_gemini_sqlite.py
import os, shutil, sqlite3

DB = os.path.join("data", "app.db")  # ajuste se o caminho for diferente

def main():
    if not os.path.exists(DB):
        raise SystemExit(f"[ERRO] Banco não encontrado: {DB}")

    # 1) backup
    bak = DB + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(DB, bak)
        print(f"[OK] Backup criado: {bak}")
    else:
        print(f"[OK] Backup já existia: {bak}")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 2) descobrir tabelas
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    if not tables:
        print("[AVISO] Nenhuma tabela encontrada no DB.")
        return

    total_updates = 0
    print(f"[INFO] Tabelas encontradas: {len(tables)} -> {tables}")

    # 3) varrer tabelas e colunas de texto trocando 1.5 -> 2.5-lite
    for t in tables:
        try:
            cur.execute(f"PRAGMA table_info({t})")
            cols = [(row[1], (row[2] or "").upper()) for row in cur.fetchall()]  # (name, type)
        except Exception as e:
            print(f"[WARN] Não consegui inspecionar {t}: {e}")
            continue

        text_cols = [c for c, typ in cols if any(k in typ for k in ("TEXT","CHAR","CLOB","JSON","VARCHAR"))]
        if not text_cols:
            continue

        for c in text_cols:
            # trocar variantes mais específicas primeiro
            for old in ("gemini-1.5-flash-002", "gemini-1.5-flash-001", "gemini-1.5-flash"):
                sql = f"UPDATE {t} SET {c} = REPLACE({c}, ?, 'gemini-2.5-flash-lite')"
                cur.execute(sql, (old,))
                if cur.rowcount:  # pode ser None em alguns drivers, mas aqui costuma vir int
                    print(f"[OK] {t}.{c}: {cur.rowcount} linhas atualizadas ({old} -> gemini-2.5-flash-lite)")
                    total_updates += cur.rowcount or 0

    conn.commit()

    # 4) verificação rápida pós-migração
    sobras = 0
    for t in tables:
        try:
            cur.execute(f"SELECT * FROM {t} LIMIT 1")  # força abrir a tabela
            cols = [d[0] for d in cur.description] if cur.description else []
            if not cols:
                continue
            like_checks = []
            for c in cols:
                like_checks.append(f"CAST({c} AS TEXT) LIKE '%gemini-1.5-flash%'")
            if like_checks:
                q = f"SELECT COUNT(*) AS n FROM {t} WHERE {' OR '.join(like_checks)}"
                cur.execute(q)
                n = cur.fetchone()[0]
                if n:
                    print(f"[ALERTA] Ainda há {n} ocorrência(s) de 'gemini-1.5-flash' em {t}")
                    sobras += n
        except Exception:
            pass

    conn.close()
    print(f"\n[RESUMO] Linhas alteradas: {total_updates}")
    if sobras == 0:
        print("[RESUMO] Nenhuma ocorrência remanescente de 'gemini-1.5-flash'. ✅")
    else:
        print("[RESUMO] Restaram {sobras} ocorrência(s). Verifique as tabelas sinalizadas. ⚠️")

if __name__ == "__main__":
    main()
