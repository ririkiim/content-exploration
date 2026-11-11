# 오프라인 네트워크 실험 결과 (DB·LLM·Wikidata 호출 없음)
시나리오(누적): S0 원본 | S1 +EXACT(코드: 기존 53 + v1.2 AUTO) | S2 +LLM/사람 SAME을 CLOSE 엣지로 | S3 +근거 있는 RELATED/BROADER | S4 대조군 +E5 단독 엣지
엣지 가중치는 사전에 고정(EXACT 1.0, SAME 0.85~0.9, QID 0.7, 파생 0.7, translit 0.6, LLM UNSURE 0.5, Wikidata BROADER 0.5, E5 단독 0.3). LLM 판정은 엣지 출처로만 사용하고 정답으로 가정하지 않음.
재실행: `python3 network_exp.py` (약 15초). 산출: network_results.json, edges.csv(6,565개, 근거·출처 포함), sample_paths.csv(1회성 31개).
