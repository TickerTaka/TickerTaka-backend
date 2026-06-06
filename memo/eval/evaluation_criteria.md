1·2·3·8 2배수 배점. 
1) Multi-Agent 구조(Supervisor-SubAgents-최종답변은 Supervisor)
2) 에러 핸들링 & 폴백 (Resilience, 각agent가 실패시 처리)
3) sLLM모델(1개이상 300B이하 모델)사용 - 검증 Agent + langfuse
4) 5대 설계문서 (유스케이스 명세서, 컴포넌트 설계서, 인터페이스 정의서, 시퀀스 다이어그램, ERD)
5) Dockerise
6) MCP or A2A 사용   
7) vLLM 사용(GPU or 맥OS)
8) 정량 평가 파이프라인 (RAGAS 등의 Evaluation 활용)
9) RAG 고도화 - Hybrid, Reranker 등
10) 스트리밍 & 비동기 처리