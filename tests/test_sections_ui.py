import pytest
from starlette.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_home_page_displays_practice_boxes_from_grade_1_to_12_and_hot_exams(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    # 1. Check 4 main sections
    assert "Học ngoại ngữ" in html
    assert "Luyện đề" in html
    assert "Tài liệu" in html
    assert "Học bài mới" in html

    # 2. Check search toolbar and new title
    assert "home-practice-search" in html or "search-filter-input" in html
    assert "Danh sách bài thi và đề thi tự luyện" in html

    # 3. Check key practice boxes (TNTHPT, ĐGNL, Lớp 12 -> Lớp 1, Đại học)
    assert "Đề thi tự luyện TNTHPT" in html
    assert "Đề thi tự luyện ĐGNL/ĐGTD" in html
    assert "Đề thi tự luyện Lớp 12" in html
    assert "Toán Lớp 12" in html
    assert "Đề thi tự luyện Lớp 11" in html
    assert "Đề thi tự luyện Lớp 10" in html
    assert "Đề thi tự luyện Lớp 9" in html
    assert "Đề thi tự luyện Lớp 8" in html
    assert "Đề thi tự luyện Lớp 7" in html
    assert "Đề thi tự luyện Lớp 6" in html
    assert "Đề thi tự luyện Lớp 5" in html
    assert "Đề thi tự luyện Lớp 4" in html
    assert "Đề thi tự luyện Lớp 3" in html
    assert "Đề thi tự luyện Lớp 2" in html
    assert "Đề thi tự luyện Lớp 1" in html
    assert "Toán Lớp 1" in html
    assert "Tiếng Việt Lớp 1" in html
    assert "Đề thi tự luyện Đại học" in html

    # 4. Check links to grade detail pages
    assert "/grade/lop-11" in html
    assert "/grade/lop-12" in html

    # 5. Check Hot section on homepage: Top những tài liệu, bài giảng và đề thi hot
    assert "Top những tài liệu, bài giảng và đề thi" in html
    assert "Tài liệu HOT" in html
    assert "Bài giảng HOT" in html
    assert "Đề thi HOT" in html

    # 6. Check no 'tracnghiem'
    assert "tracnghiem" not in html.lower()


def test_section_detail_page_shows_levels_and_hides_practice_boxes(client):
    for sec_id in ["luyen-de", "tai-lieu", "hoc-bai-moi"]:
        response = client.get(f"/section/{sec_id}")
        assert response.status_code == 200
        html = response.text
        # Shows category levels
        assert "Tiểu học" in html
        assert "THCS" in html
        assert "THPT" in html
        assert "Đại học" in html
        # Practice boxes should not show in section pages
        assert 'id="home-boxes"' not in html

    # Ngoại ngữ has 3 main languages: Tiếng Anh, Tiếng Trung, Tiếng Nhật
    res_nn = client.get("/section/ngoai-ngu")
    assert res_nn.status_code == 200
    assert "Tiếng Anh" in res_nn.text
    assert "Tiếng Trung" in res_nn.text
    assert "Tiếng Nhật" in res_nn.text
    assert 'id="home-boxes"' not in res_nn.text


def test_all_four_sections_have_separated_individual_grades(client):
    # 1. Luyện đề
    res_ld = client.get("/section/luyen-de")
    assert res_ld.status_code == 200
    assert "Luyện đề Lớp 1" in res_ld.text
    assert "Luyện đề Lớp 2" in res_ld.text
    assert "Luyện đề Lớp 12" in res_ld.text
    assert "/grade/lop-1/subject/toan-lop-1" in res_ld.text
    assert "Vào phòng luyện đề →" in res_ld.text

    # 2. Học bài mới
    res_bm = client.get("/section/hoc-bai-moi")
    assert res_bm.status_code == 200
    assert "Học bài mới Lớp 1" in res_bm.text
    assert "Học bài mới Lớp 2" in res_bm.text
    assert "Học bài mới Lớp 12" in res_bm.text
    assert "/grade/lop-1/subject/toan-lop-1" in res_bm.text
    assert "Học lý thuyết & Làm bài củng cố →" in res_bm.text

    # 3. Tài liệu
    res_tl = client.get("/section/tai-lieu")
    assert res_tl.status_code == 200
    assert "Đề cương Lớp 1" in res_tl.text
    assert "Đề cương Lớp 2" in res_tl.text
    assert "Đề cương Lớp 12" in res_tl.text
    assert "/grade/lop-1/subject/toan-lop-1" in res_tl.text
    assert "Xem tài liệu &amp; Đề cương →" in res_tl.text or "Xem tài liệu" in res_tl.text

    # 4. Học ngoại ngữ: 3 loại chính Tiếng Anh, Tiếng Trung, Tiếng Nhật & TOEIC, IELTS, VSTEP, HSK, JLPT
    res_nn = client.get("/section/ngoai-ngu")
    assert res_nn.status_code == 200
    assert "Tiếng Anh" in res_nn.text
    assert "Tiếng Trung" in res_nn.text
    assert "Tiếng Nhật" in res_nn.text
    assert "TOEIC" in res_nn.text
    assert "IELTS" in res_nn.text
    assert "VSTEP" in res_nn.text
    assert "HSK" in res_nn.text
    assert "JLPT" in res_nn.text
    assert (
        "Vào học ngay Luyện thi TOEIC" in res_nn.text
        or "Vào luyện đề & học bài →" in res_nn.text
        or "Vào mục Tiếng Anh" in res_nn.text
    )  # noqa: E501


def test_sections_have_dedicated_hot_blocks(client):
    # Luyện đề -> đề thi hot
    res_ld = client.get("/section/luyen-de")
    assert res_ld.status_code == 200
    assert "Top những đề thi HOT & Được làm nhiều nhất" in res_ld.text

    # Tài liệu -> tài liệu hot
    res_tl = client.get("/section/tai-lieu")
    assert res_tl.status_code == 200
    assert "Top những tài liệu & Đề cương HOT nhất" in res_tl.text
    assert "Sổ tay công thức Vật lí 11" in res_tl.text

    # Học bài mới -> bài giảng hot
    res_bm = client.get("/section/hoc-bai-moi")
    assert res_bm.status_code == 200
    assert "Top những bài giảng & Lý thuyết HOT nhất" in res_bm.text
    assert "Dao động điều hòa" in res_bm.text

    # Ngoại ngữ -> chuyên đề & đề thi ngoại ngữ hot
    res_nn = client.get("/section/ngoai-ngu")
    assert res_nn.status_code == 200
    assert "Top chuyên đề từ vựng & Đề thi ngoại ngữ HOT nhất" in res_nn.text
    assert "TOEIC" in res_nn.text or "Tiếng Anh" in res_nn.text


def test_grade_detail_page(client):
    response = client.get("/grade/lop-11")
    assert response.status_code == 200
    html = response.text
    assert "Lớp 11" in html
    assert "Vật Lí" in html
    assert "Toán Lớp 11" in html
    assert "/grade/lop-11/subject/vat-li-lop-11" in html
    assert "/grade/lop-11/subject/toan-lop-11" in html


def test_grade_1_and_grade_2_curriculum_pages(client):
    # Lớp 1
    res_g1 = client.get("/grade/lop-1")
    assert res_g1.status_code == 200
    assert "Toán Lớp 1" in res_g1.text
    assert "Tiếng Việt Lớp 1" in res_g1.text

    res_toan1 = client.get("/grade/lop-1/subject/toan-lop-1")
    assert res_toan1.status_code == 200
    assert "Toán Lớp 1" in res_toan1.text
    assert "Chương 1" in res_toan1.text
    assert "Chương 2" in res_toan1.text
    assert "Chương 3" in res_toan1.text

    # Lớp 2
    res_g2 = client.get("/grade/lop-2")
    assert res_g2.status_code == 200
    assert "Toán Lớp 2" in res_g2.text

    res_toan2 = client.get("/grade/lop-2/subject/toan-lop-2")
    assert res_toan2.status_code == 200
    assert "Toán Lớp 2" in res_toan2.text
    assert "Chương 1" in res_toan2.text
    assert "Chương 2" in res_toan2.text


def test_grade_subject_curriculum_page(client):
    response = client.get("/grade/lop-11/subject/vat-li-lop-11")
    assert response.status_code == 200
    html = response.text
    assert "Vật lí 11" in html
    assert "Chương 1" in html
    assert "Chương 2" in html
    assert "Bài 1" in html
    # 1. "Luyện tập bài học" renamed to "Luyện đề trực tuyến"
    assert "Luyện đề trực tuyến" in html
    assert "Luyện tập bài học" not in html
    # 2. "Đề thi có sẵn của chương" removed
    assert "Đề thi có sẵn của chương" not in html
    # 3. Table of contents & accordion exists
    assert "Mục lục chương" in html
    assert "toggleChapter" in html
    assert "chapter-toggle-head" in html


def test_foreign_languages_portal_and_sub_pages(client):
    # 1. Section Ngoại ngữ: 3 mảng Tiếng Anh, Tiếng Trung, Tiếng Nhật
    res = client.get("/section/ngoai-ngu")
    assert res.status_code == 200
    assert "Tiếng Anh" in res.text
    assert "Tiếng Trung" in res.text
    assert "Tiếng Nhật" in res.text
    assert "TOEIC" in res.text
    assert "IELTS" in res.text
    assert "HSK" in res.text
    assert "JLPT" in res.text

    # 2. Trang Tiếng Anh riêng (/grade/tieng-anh): có TOEIC và IELTS
    res_en = client.get("/grade/tieng-anh")
    assert res_en.status_code == 200
    assert "TOEIC" in res_en.text
    assert "IELTS" in res_en.text

    # 3. Khi ấn vào TOEIC (/grade/tieng-anh/subject/toeic):
    # Có bài test năng lực, Study (Listening, Reading, Writing, Speaking) và Practice
    res_toeic = client.get("/grade/tieng-anh/subject/toeic")
    assert res_toeic.status_code == 200
    assert "Test Năng Lực" in res_toeic.text or "test năng lực" in res_toeic.text.lower()
    assert "PHẦN STUDY" in res_toeic.text
    assert "PHẦN PRACTICE" in res_toeic.text
    assert "Listening" in res_toeic.text
    assert "Reading" in res_toeic.text
    assert "Writing" in res_toeic.text
    assert "Speaking" in res_toeic.text

    # 4. Khi ấn vào IELTS (/grade/tieng-anh/subject/ielts):
    res_ielts = client.get("/grade/tieng-anh/subject/ielts")
    assert res_ielts.status_code == 200
    assert "Test Năng Lực" in res_ielts.text or "test" in res_ielts.text.lower()
    assert "PHẦN STUDY" in res_ielts.text
    assert "PHẦN PRACTICE" in res_ielts.text
    assert "Listening" in res_ielts.text
    assert "Reading" in res_ielts.text
    assert "Writing" in res_ielts.text
    assert "Speaking" in res_ielts.text

    # 5. Trang Tiếng Trung riêng (/grade/tieng-trung): có HSK và HSKK
    res_cn = client.get("/grade/tieng-trung")
    assert res_cn.status_code == 200
    assert "HSK" in res_cn.text

    # 6. Khi ấn vào HSK (/grade/tieng-trung/subject/hsk):
    res_hsk = client.get("/grade/tieng-trung/subject/hsk")
    assert res_hsk.status_code == 200
    assert "Test Năng Lực" in res_hsk.text or "test" in res_hsk.text.lower()
    assert "PHẦN STUDY" in res_hsk.text
    assert "PHẦN PRACTICE" in res_hsk.text
    assert "Listening" in res_hsk.text or "听力" in res_hsk.text
    assert "Reading" in res_hsk.text or "阅读" in res_hsk.text
    assert "Writing" in res_hsk.text or "书写" in res_hsk.text
    assert "Speaking" in res_hsk.text or "口语" in res_hsk.text

    # 7. Trang Tiếng Nhật riêng (/grade/tieng-nhat): có JLPT
    res_jp = client.get("/grade/tieng-nhat")
    assert res_jp.status_code == 200
    assert "JLPT" in res_jp.text

    # 8. Khi ấn vào JLPT (/grade/tieng-nhat/subject/jlpt):
    res_jlpt = client.get("/grade/tieng-nhat/subject/jlpt")
    assert res_jlpt.status_code == 200
    assert "Test Năng Lực" in res_jlpt.text or "test" in res_jlpt.text.lower()
    assert "PHẦN STUDY" in res_jlpt.text
    assert "PHẦN PRACTICE" in res_jlpt.text
    assert "Listening" in res_jlpt.text or "聴解" in res_jlpt.text
    assert "Reading" in res_jlpt.text or "読解" in res_jlpt.text

    # 9. Kiểm tra trang Study và Practice riêng biệt của TOEIC
    res_toeic_study = client.get("/grade/tieng-anh/subject/toeic/study")
    assert res_toeic_study.status_code == 200
    assert "PHẦN STUDY" in res_toeic_study.text
    assert "/grade/tieng-anh/subject/toeic" in res_toeic_study.text
    assert "Chuyển sang phần Practice" in res_toeic_study.text

    res_toeic_practice = client.get("/grade/tieng-anh/subject/toeic/practice")
    assert res_toeic_practice.status_code == 200
    assert "PHẦN PRACTICE" in res_toeic_practice.text
    assert "/grade/tieng-anh/subject/toeic" in res_toeic_practice.text
    assert "Chuyển sang phần Study" in res_toeic_practice.text


def test_materials_three_parts_and_class_curriculum_pages(client):
    # 1. Trang danh mục chính Tài liệu có 3 phần
    res_tl = client.get("/section/tai-lieu")
    assert res_tl.status_code == 200
    assert "PHẦN 01" in res_tl.text
    assert "Tài liệu & Bài giảng trên lớp" in res_tl.text
    assert "PHẦN 02" in res_tl.text
    assert "Tài liệu thêm & Sách tham khảo" in res_tl.text
    assert "PHẦN 03" in res_tl.text
    assert "Một số đề thi tham khảo" in res_tl.text
    assert "/section/tai-lieu/level/tieu-hoc/chinh-khoa" in res_tl.text
    assert "/section/tai-lieu/level/thcs/chinh-khoa" in res_tl.text
    assert "/section/tai-lieu/level/thpt/chinh-khoa" in res_tl.text
    assert "/section/tai-lieu/level/dai-hoc/chinh-khoa" in res_tl.text

    # 2. Trang mới của Tiểu học (Lớp 1 - 5)
    res_th = client.get("/section/tai-lieu/level/tieu-hoc/chinh-khoa")
    assert res_th.status_code == 200
    assert "Tài liệu & Đề cương bài giảng: Tiểu học" in res_th.text
    assert "Lớp 1" in res_th.text
    assert "Lớp 2" in res_th.text
    assert "Lớp 3" in res_th.text
    assert "Lớp 4" in res_th.text
    assert "Lớp 5" in res_th.text
    assert "Toán Lớp 1" in res_th.text
    assert "Tiếng Việt Lớp 1" in res_th.text
    assert "← Quay lại chuyên mục Tài liệu" in res_th.text

    # 3. Trang mới của THCS (Lớp 6 - 9)
    res_thcs = client.get("/section/tai-lieu/level/thcs/chinh-khoa")
    assert res_thcs.status_code == 200
    assert "Lớp 6" in res_thcs.text
    assert "Lớp 7" in res_thcs.text
    assert "Lớp 8" in res_thcs.text
    assert "Lớp 9" in res_thcs.text
    assert "Toán Lớp 9" in res_thcs.text

    # 4. Trang mới của THPT (Lớp 10 - 12)
    res_thpt = client.get("/section/tai-lieu/level/thpt/chinh-khoa")
    assert res_thpt.status_code == 200
    assert "Lớp 10" in res_thpt.text
    assert "Lớp 11" in res_thpt.text
    assert "Lớp 12" in res_thpt.text
    assert "TNTHPT" in res_thpt.text or "Ôn thi Quốc gia" in res_thpt.text

    # 5. Trang mới của Đại học
    res_dh = client.get("/section/tai-lieu/level/dai-hoc/chinh-khoa")
    assert res_dh.status_code == 200
    assert "Triết học Mác - Lênin" in res_dh.text
    assert "Kinh tế chính trị" in res_dh.text


def test_crawler_pipeline_open_apis():
    import json

    from app.pipeline.domain.models import ParsedPage
    from app.pipeline.parsers.base import all_parsers

    # 1. Verify opentrivia_quiz parser is registered
    parsers = all_parsers()
    assert "opentrivia_quiz" in parsers

    # 2. Test opentrivia parser logic with sample JSON payload
    sample_json = json.dumps(
        {
            "response_code": 0,
            "results": [
                {
                    "type": "multiple",
                    "difficulty": "medium",
                    "category": "Science: Mathematics",
                    "question": "What is the square root of 64?",
                    "correct_answer": "8",
                    "incorrect_answers": ["6", "7", "9"],
                },
                {
                    "type": "boolean",
                    "difficulty": "easy",
                    "category": "Science: Computers",
                    "question": "HTML stands for HyperText Markup Language.",
                    "correct_answer": "True",
                    "incorrect_answers": ["False"],
                },
            ],
        }
    )
    parser_fn = parsers["opentrivia_quiz"]
    page: ParsedPage = parser_fn(sample_json, "https://opentdb.com/api.php?amount=2")
    assert len(page.questions) == 2

    # Verify first question
    q1 = page.questions[0]
    assert q1.content == "What is the square root of 64?"
    assert q1.question_type == "MULTIPLE_CHOICE"
    assert q1.difficulty == "UNDERSTANDING"
    assert len(q1.choices) == 4
    correct_choices = [c for c in q1.choices if c["is_correct"]]
    assert len(correct_choices) == 1
    assert correct_choices[0]["content"] == "8"

    # Verify second question
    q2 = page.questions[1]
    assert q2.content == "HTML stands for HyperText Markup Language."
    assert q2.difficulty == "RECOGNITION"
    assert len(q2.choices) == 2
    correct_tf = [c for c in q2.choices if c["is_correct"]]
    assert len(correct_tf) == 1
    assert correct_tf[0]["content"] == "True"
