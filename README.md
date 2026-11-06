### 1. 개인화 관련 DB 테이블

**① `user_profile`**
- `user_id`, `external_user_key`, `created_at`, `updated_at`
- 사용자 식별 및 프로필 관리

**② `user_preference`**
- `preference_id`, `user_id`, `preference_type`, `preference_value`, `target_content_type`, `preference_weight`, `preference_source`, `created_at`, `updated_at`
- 초기 선호 및 명시적·암묵적 선호 저장

**③ `user_interaction`**
- `event_id`, `user_id`, `session_id`, `parent_event_id`, `event_type`, `target_type`, `entity_id`, `content_id`, `source_content_id`, `selected_entity_id`, `created_at`
- 사용자 탐색 행동 및 관계 경로 기록

### 2. 신규 Python 파일 및 역할

- `user_preference.py`: 사용자 초기 선호 저장·조회
- `preference_profile.py`: 선호 가중치 및 시간 감쇠 계산
- `user_interaction.py`: 콘텐츠·Entity 선택, 조회, 좋아요/싫어요 등 행동 기록
- `interaction_preference.py`: 행동별 가중치, 중복 처리, 시간 감쇠 및 관심 신호 계산
- `preference_feature_mapping.py`: 행동 신호를 장르·토픽·탐색 성향 등으로 변환 (구현 및 테스트 완료, 추가 개발 보류)
- `unified_preference_profile.py`: 초기 선호, 행동 기반 관심 신호, 관계 탐색 이력 통합

### 3. 구현 완료 기능

- 사용자 초기 선호 관리 및 시간 감쇠 적용
- 사용자 행동 기록 및 세션별 관계 탐색 경로 추적
- 명시적 평가(좋아요/싫어요)와 암묵적 관심(선택/조회) 구분
- 반복 행동 중복 처리 및 최신 평가 반영
- Entity·Content별 관심 점수 계산
- 초기 선호·행동 신호·탐색 이력을 통합한 개인화 프로필 생성
- DB 연동 및 주요 기능 테스트 통과

### 4. 추후 계획

1. 초기 선호와 행동 이력이 함께 존재하는 통합 테스트
2. 후보 정제·점수화 결과와 연결할 데이터 형식 협의
3. 관계 품질 점수를 유지하면서 사용자 선호·탐색 이력을 반영하는 최종 재랭킹 구현
4. 개인화 적용 전후 추천 순위 비교 및 성능 평가