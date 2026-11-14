"""Detailed curriculum data (Chương, Bài) for subjects across grades,
supporting Lớp 11, Lớp 12, Lớp 10, THCS, Tiểu học, ĐGNL, TNTHPT, and Đại học.
"""

import re
from typing import Any

# Comprehensive curriculum tree for prominent subjects
CURRICULUM_DATA: dict[str, dict[str, Any]] = {
    # Lớp 1: Toán Lớp 1
    "lop-1_toan-lop-1": {
        "subject_name": "Toán Lớp 1",
        "grade_name": "Lớp 1",
        "grade_id": "lop-1",
        "icon": "📐",
        "color": "#2563eb",
        "description": "Chương trình Toán 1 SGK mới: Các số từ 0 đến 100, phép tính cộng trừ không nhớ, hình học và đo lường trực quan.",
        "chapters": [
            {
                "id": "c1",
                "title": "Chương 1: Các số từ 0 đến 10 & Hình học trực quan",
                "desc": "Nhận biết các số từ 0 đến 10, so sánh số và làm quen các hình khối cơ bản.",
                "lessons": [
                    {"id": "b1", "title": "Bài 1: Các số 1, 2, 3", "badge": "Nhận biết", "questions_count": 15},
                    {"id": "b2", "title": "Bài 2: Các số 4, 5, 6", "badge": "Nhận biết", "questions_count": 15},
                    {
                        "id": "b3",
                        "title": "Bài 3: Các số 7, 8, 9, 10 và số 0",
                        "badge": "Nhận biết",
                        "questions_count": 20,
                    },
                    {
                        "id": "b4",
                        "title": "Bài 4: Hình vuông, hình tròn, hình tam giác, hình chữ nhật",
                        "badge": "Hình học",
                        "questions_count": 15,
                    },
                    {
                        "id": "b5",
                        "title": "Bài 5: So sánh số: Nhiều hơn, ít hơn, bằng nhau (<, >, =)",
                        "badge": "So sánh",
                        "questions_count": 20,
                    },
                    {
                        "id": "b-ot1",
                        "title": "Ôn tập & Đề kiểm tra Chương 1 Toán 1",
                        "badge": "Đề tự luyện",
                        "is_exam": True,
                        "questions_count": 25,
                    },
                ],
            },
            {
                "id": "c2",
                "title": "Chương 2: Phép cộng và phép trừ trong phạm vi 10",
                "desc": "Khái niệm phép cộng, phép trừ và bảng cộng trừ trong phạm vi 10.",
                "lessons": [
                    {
                        "id": "b6",
                        "title": "Bài 6: Phép cộng trong phạm vi 10",
                        "badge": "Phép cộng",
                        "questions_count": 20,
                    },
                    {
                        "id": "b7",
                        "title": "Bài 7: Phép trừ trong phạm vi 10",
                        "badge": "Phép trừ",
                        "questions_count": 20,
                    },
                    {
                        "id": "b8",
                        "title": "Bài 8: Mối quan hệ giữa phép cộng và phép trừ",
                        "badge": "Vận dụng",
                        "questions_count": 20,
                    },
                    {
                        "id": "b9",
                        "title": "Bài 9: Bảng cộng và bảng trừ trong phạm vi 10",
                        "badge": "Thuộc lòng",
                        "questions_count": 25,
                    },
                    {
                        "id": "b-ot2",
                        "title": "Ôn tập & Đề kiểm tra Chương 2 Toán 1",
                        "badge": "Đề tự luyện",
                        "is_exam": True,
                        "questions_count": 30,
                    },
                ],
            },
            {
                "id": "c3",
                "title": "Chương 3: Các số trong phạm vi 100 & Đo độ dài",
                "desc": "Các số tròn chục, đọc viết số đến 99, làm quen đơn vị đo độ dài cm.",
                "lessons": [
                    {"id": "b10", "title": "Bài 10: Các số tròn chục", "badge": "Số học", "questions_count": 15},
                    {
                        "id": "b11",
                        "title": "Bài 11: Đo độ dài - Đơn vị xăng-ti-mét (cm)",
                        "badge": "Đo lường",
                        "questions_count": 15,
                    },
                    {
                        "id": "b12",
                        "title": "Bài 12: Đọc, viết, so sánh các số từ 11 đến 99",
                        "badge": "Số học",
                        "questions_count": 25,
                    },
                    {
                        "id": "b13",
                        "title": "Bài 13: Số 100 và bảng các số từ 1 đến 100",
                        "badge": "Tổng kết số",
                        "questions_count": 20,
                    },
                    {
                        "id": "b-ot3",
                        "title": "Ôn tập & Đề kiểm tra Chương 3 Toán 1",
                        "badge": "Đề tự luyện",
                        "is_exam": True,
                        "questions_count": 30,
                    },
                ],
            },
            {
                "id": "c4",
                "title": "Chương 4: Phép cộng, trừ không nhớ trong phạm vi 100 & Xem giờ",
                "desc": "Cộng trừ số có 2 chữ số không nhớ, làm quen đồng hồ và lịch.",
                "lessons": [
                    {
                        "id": "b14",
                        "title": "Bài 14: Phép cộng dạng 25 + 14 và 30 + 20",
                        "badge": "Cộng 2 chữ số",
                        "questions_count": 20,
                    },
                    {
                        "id": "b15",
                        "title": "Bài 15: Phép trừ dạng 39 - 15 và 50 - 20",
                        "badge": "Trừ 2 chữ số",
                        "questions_count": 20,
                    },
                    {
                        "id": "b16",
                        "title": "Bài 16: Đồng hồ, thời gian và các ngày trong tuần",
                        "badge": "Thực tế",
                        "questions_count": 15,
                    },
                    {
                        "id": "b17",
                        "title": "Bài 17: Bộ đề kiểm tra học kỳ 2 Toán Lớp 1",
                        "badge": "Thi học kỳ",
                        "is_exam": True,
                        "questions_count": 35,
                    },
                ],
            },
        ],
    },
    # Lớp 2: Toán Lớp 2
    "lop-2_toan-lop-2": {
        "subject_name": "Toán Lớp 2",
        "grade_name": "Lớp 2",
        "grade_id": "lop-2",
        "icon": "📐",
        "color": "#2563eb",
        "description": "Chương trình Toán 2 SGK mới: Phép cộng trừ có nhớ trong phạm vi 100, bảng nhân chia 2 và 5, số đến 1000.",
        "chapters": [
            {
                "id": "c1",
                "title": "Chương 1: Ôn tập và phép cộng, trừ có nhớ trong phạm vi 100",
                "desc": "Rèn luyện kỹ năng tính toán cộng trừ có nhớ số có hai chữ số.",
                "lessons": [
                    {
                        "id": "b1",
                        "title": "Bài 1: Ôn tập các số đến 100 và phép tính",
                        "badge": "Ôn tập",
                        "questions_count": 20,
                    },
                    {
                        "id": "b2",
                        "title": "Bài 2: Phép cộng có nhớ trong phạm vi 100",
                        "badge": "Trọng tâm",
                        "questions_count": 25,
                    },
                    {
                        "id": "b3",
                        "title": "Bài 3: Phép trừ có nhớ trong phạm vi 100",
                        "badge": "Trọng tâm",
                        "questions_count": 25,
                    },
                    {
                        "id": "b4",
                        "title": "Bài 4: Điểm, đoạn thẳng, đường cong, đường gấp khúc",
                        "badge": "Hình học",
                        "questions_count": 15,
                    },
                    {
                        "id": "b-ot1",
                        "title": "Ôn tập & Đề kiểm tra Chương 1 Toán 2",
                        "badge": "Đề tự luyện",
                        "is_exam": True,
                        "questions_count": 30,
                    },
                ],
            },
            {
                "id": "c2",
                "title": "Chương 2: Phép nhân và phép chia (Bảng 2 và 5)",
                "desc": "Hình thành khái niệm phép nhân, phép chia, bảng nhân 2, bảng nhân 5, chia 2, chia 5.",
                "lessons": [
                    {
                        "id": "b5",
                        "title": "Bài 5: Làm quen với phép nhân và thừa số, tích",
                        "badge": "Khái niệm",
                        "questions_count": 20,
                    },
                    {
                        "id": "b6",
                        "title": "Bài 6: Bảng nhân 2 và Bảng nhân 5",
                        "badge": "Bảng nhân",
                        "questions_count": 25,
                    },
                    {
                        "id": "b7",
                        "title": "Bài 7: Làm quen với phép chia và số bị chia, số chia, thương",
                        "badge": "Khái niệm",
                        "questions_count": 20,
                    },
                    {
                        "id": "b8",
                        "title": "Bài 8: Bảng chia 2 và Bảng chia 5",
                        "badge": "Bảng chia",
                        "questions_count": 25,
                    },
                    {
                        "id": "b-ot2",
                        "title": "Ôn tập & Đề kiểm tra Chương 2 Toán 2",
                        "badge": "Đề tự luyện",
                        "is_exam": True,
                        "questions_count": 30,
                    },
                ],
            },
            {
                "id": "c3",
                "title": "Chương 3: Các số trong phạm vi 1000 & Đơn vị đo lường",
                "desc": "Đọc viết số có ba chữ số, so sánh số, làm quen đơn vị dm, m, km, kg, l.",
                "lessons": [
                    {
                        "id": "b9",
                        "title": "Bài 9: Đơn vị đo: Đề-xi-mét (dm), Mét (m), Ki-lô-mét (km)",
                        "badge": "Đo độ dài",
                        "questions_count": 20,
                    },
                    {
                        "id": "b10",
                        "title": "Bài 10: Đơn vị đo khối lượng: Ki-lô-gam (kg), Lít (l)",
                        "badge": "Đo lường",
                        "questions_count": 20,
                    },
                    {
                        "id": "b11",
                        "title": "Bài 11: Các số có ba chữ số và số 1000",
                        "badge": "Số học",
                        "questions_count": 25,
                    },
                    {
                        "id": "b12",
                        "title": "Bài 12: So sánh các số trong phạm vi 1000",
                        "badge": "So sánh",
                        "questions_count": 20,
                    },
                    {
                        "id": "b-ot3",
                        "title": "Ôn tập & Đề kiểm tra Chương 3 Toán 2",
                        "badge": "Đề tự luyện",
                        "is_exam": True,
                        "questions_count": 30,
                    },
                ],
            },
            {
                "id": "c4",
                "title": "Chương 4: Phép cộng, trừ trong phạm vi 1000 & Tổng kết cuối năm",
                "desc": "Cộng trừ số có 3 chữ số, hình khối trực quan và tổng ôn thi cuối năm.",
                "lessons": [
                    {
                        "id": "b13",
                        "title": "Bài 13: Phép cộng không nhớ và có nhớ trong phạm vi 1000",
                        "badge": "Tính toán",
                        "questions_count": 25,
                    },
                    {
                        "id": "b14",
                        "title": "Bài 14: Phép trừ không nhớ và có nhớ trong phạm vi 1000",
                        "badge": "Tính toán",
                        "questions_count": 25,
                    },
                    {
                        "id": "b15",
                        "title": "Bài 15: Khối trụ, khối cầu và thực hành xem đồng hồ",
                        "badge": "Thực tế",
                        "questions_count": 15,
                    },
                    {
                        "id": "b16",
                        "title": "Bài 16: Bộ đề thi học kỳ 2 Toán Lớp 2 chuẩn",
                        "badge": "Thi học kỳ",
                        "is_exam": True,
                        "questions_count": 35,
                    },
                ],
            },
        ],
    },
    # Lớp 3: Toán Lớp 3
    "lop-3_toan-lop-3": {
        "subject_name": "Toán Lớp 3",
        "grade_name": "Lớp 3",
        "grade_id": "lop-3",
        "icon": "📐",
        "color": "#2563eb",
        "description": "Chương trình Toán 3 SGK mới: Bảng nhân chia 3 đến 9, số đến 100.000, chu vi và diện tích hình phẳng.",
        "chapters": [
            {
                "id": "c1",
                "title": "Chương 1: Bảng nhân, bảng chia từ 3 đến 9 & Góc vuông",
                "desc": "Hoàn thiện bảng cửu chương và khái niệm góc vuông, tam giác, tứ giác.",
                "lessons": [
                    {
                        "id": "b1",
                        "title": "Bài 1: Bảng nhân và bảng chia 3, 4, 6",
                        "badge": "Cửu chương",
                        "questions_count": 25,
                    },
                    {
                        "id": "b2",
                        "title": "Bài 2: Bảng nhân và bảng chia 7, 8, 9",
                        "badge": "Cửu chương",
                        "questions_count": 25,
                    },
                    {
                        "id": "b3",
                        "title": "Bài 3: Góc vuông, góc không vuông và ê-ke",
                        "badge": "Hình học",
                        "questions_count": 20,
                    },
                    {
                        "id": "b-ot1",
                        "title": "Ôn tập & Đề kiểm tra Chương 1 Toán 3",
                        "badge": "Đề tự luyện",
                        "is_exam": True,
                        "questions_count": 30,
                    },
                ],
            },
            {
                "id": "c2",
                "title": "Chương 2: Các số trong phạm vi 10 000 & Chu vi, diện tích",
                "desc": "Số có bốn chữ số, cộng trừ nhân chia và công thức chu vi, diện tích hình chữ nhật, hình vuông.",
                "lessons": [
                    {
                        "id": "b4",
                        "title": "Bài 4: Số có bốn chữ số và phép cộng, trừ phạm vi 10 000",
                        "badge": "Tính toán",
                        "questions_count": 25,
                    },
                    {
                        "id": "b5",
                        "title": "Bài 5: Chu vi hình chữ nhật, hình vuông",
                        "badge": "Hình học",
                        "questions_count": 20,
                    },
                    {
                        "id": "b6",
                        "title": "Bài 6: Diện tích hình chữ nhật, hình vuông (cm2)",
                        "badge": "Diện tích",
                        "questions_count": 20,
                    },
                    {
                        "id": "b-ot2",
                        "title": "Ôn tập & Đề kiểm tra Chương 2 Toán 3",
                        "badge": "Đề tự luyện",
                        "is_exam": True,
                        "questions_count": 30,
                    },
                ],
            },
            {
                "id": "c3",
                "title": "Chương 3: Các số trong phạm vi 100 000 & Thống kê",
                "desc": "Đọc viết số đến 100 000, nhân chia với số có một chữ số, làm quen tiền Việt Nam và bảng số liệu.",
                "lessons": [
                    {
                        "id": "b7",
                        "title": "Bài 7: Số có năm chữ số và phép tính phạm vi 100 000",
                        "badge": "Số học",
                        "questions_count": 25,
                    },
                    {
                        "id": "b8",
                        "title": "Bài 8: Tiền Việt Nam và bài toán mua sắm",
                        "badge": "Thực tế",
                        "questions_count": 20,
                    },
                    {
                        "id": "b9",
                        "title": "Bài 9: Bảng số liệu thống kê và khả năng xảy ra của sự kiện",
                        "badge": "Xác suất",
                        "questions_count": 15,
                    },
                    {
                        "id": "b10",
                        "title": "Bài 10: Bộ đề thi học kỳ 2 Toán Lớp 3",
                        "badge": "Thi học kỳ",
                        "is_exam": True,
                        "questions_count": 35,
                    },
                ],
            },
        ],
    },
    # Lớp 4: Toán Lớp 4
    "lop-4_toan-lop-4": {
        "subject_name": "Toán Lớp 4",
        "grade_name": "Lớp 4",
        "grade_id": "lop-4",
        "icon": "📐",
        "color": "#2563eb",
        "description": "Chương trình Toán 4 SGK mới: Số có nhiều chữ số, phân số và các phép tính phân số, hình bình hành, hình thoi.",
        "chapters": [
            {
                "id": "c1",
                "title": "Chương 1: Số có nhiều chữ số & Phép tính số tự nhiên",
                "desc": "Hàng và lớp, so sánh số, tính chất giao hoán và kết hợp của phép tính.",
                "lessons": [
                    {
                        "id": "b1",
                        "title": "Bài 1: Số có sáu chữ số, Hàng và lớp",
                        "badge": "Số học",
                        "questions_count": 25,
                    },
                    {"id": "b2", "title": "Bài 2: Triệu và lớp triệu", "badge": "Số lớn", "questions_count": 20},
                    {
                        "id": "b3",
                        "title": "Bài 3: Phép cộng và phép trừ số có nhiều chữ số",
                        "badge": "Tính toán",
                        "questions_count": 25,
                    },
                    {
                        "id": "b4",
                        "title": "Bài 4: Phép nhân và phép chia cho số có hai chữ số",
                        "badge": "Nhân chia",
                        "questions_count": 25,
                    },
                    {
                        "id": "b-ot1",
                        "title": "Ôn tập & Đề kiểm tra Chương 1 Toán 4",
                        "badge": "Đề tự luyện",
                        "is_exam": True,
                        "questions_count": 30,
                    },
                ],
            },
            {
                "id": "c2",
                "title": "Chương 2: Phân số & Bốn phép tính với phân số",
                "desc": "Khái niệm phân số, phân số bằng nhau, quy đồng mẫu số và 4 phép tính cộng, trừ, nhân, chia phân số.",
                "lessons": [
                    {
                        "id": "b5",
                        "title": "Bài 5: Khái niệm phân số và phân số bằng nhau",
                        "badge": "Phân số",
                        "questions_count": 20,
                    },
                    {
                        "id": "b6",
                        "title": "Bài 6: Rút gọn và quy đồng mẫu số các phân số",
                        "badge": "Kỹ năng",
                        "questions_count": 25,
                    },
                    {
                        "id": "b7",
                        "title": "Bài 7: Phép cộng và phép trừ phân số",
                        "badge": "Tính toán",
                        "questions_count": 25,
                    },
                    {
                        "id": "b8",
                        "title": "Bài 8: Phép nhân và phép chia phân số",
                        "badge": "Tính toán",
                        "questions_count": 25,
                    },
                    {
                        "id": "b-ot2",
                        "title": "Ôn tập & Đề kiểm tra Chương 2 Toán 4",
                        "badge": "Đề tự luyện",
                        "is_exam": True,
                        "questions_count": 30,
                    },
                ],
            },
            {
                "id": "c3",
                "title": "Chương 3: Hình học & Đại lượng đo diện tích",
                "desc": "Hình bình hành, hình thoi, diện tích hình bình hành, hình thoi và đơn vị đo dm2, m2, mm2.",
                "lessons": [
                    {
                        "id": "b9",
                        "title": "Bài 9: Hình bình hành và diện tích hình bình hành",
                        "badge": "Hình học",
                        "questions_count": 20,
                    },
                    {
                        "id": "b10",
                        "title": "Bài 10: Hình thoi và diện tích hình thoi",
                        "badge": "Hình học",
                        "questions_count": 20,
                    },
                    {
                        "id": "b11",
                        "title": "Bài 11: Đơn vị đo diện tích: Đề-xi-mét vuông, mét vuông, mi-li-mét vuông",
                        "badge": "Đo lường",
                        "questions_count": 20,
                    },
                    {
                        "id": "b12",
                        "title": "Bài 12: Bộ đề thi học kỳ 2 Toán Lớp 4 chuẩn",
                        "badge": "Thi học kỳ",
                        "is_exam": True,
                        "questions_count": 35,
                    },
                ],
            },
        ],
    },
    # Lớp 5: Toán Lớp 5
    "lop-5_toan-lop-5": {
        "subject_name": "Toán Lớp 5",
        "grade_name": "Lớp 5",
        "grade_id": "lop-5",
        "icon": "📐",
        "color": "#2563eb",
        "description": "Chương trình Toán 5 SGK mới: Số thập phân, tỉ số phần trăm, hình học không gian và toán chuyển động đều.",
        "chapters": [
            {
                "id": "c1",
                "title": "Chương 1: Phân số thập phân & Số thập phân",
                "desc": "Khái niệm số thập phân, hàng của số thập phân, đọc viết và so sánh số thập phân.",
                "lessons": [
                    {
                        "id": "b1",
                        "title": "Bài 1: Khái niệm số thập phân và phân số thập phân",
                        "badge": "Lý thuyết",
                        "questions_count": 20,
                    },
                    {
                        "id": "b2",
                        "title": "Bài 2: Hàng của số thập phân và đọc viết số thập phân",
                        "badge": "Số học",
                        "questions_count": 25,
                    },
                    {"id": "b3", "title": "Bài 3: So sánh hai số thập phân", "badge": "So sánh", "questions_count": 20},
                    {
                        "id": "b-ot1",
                        "title": "Ôn tập & Đề kiểm tra Chương 1 Toán 5",
                        "badge": "Đề tự luyện",
                        "is_exam": True,
                        "questions_count": 30,
                    },
                ],
            },
            {
                "id": "c2",
                "title": "Chương 2: Các phép tính với số thập phân & Tỉ số phần trăm",
                "desc": "Cộng, trừ, nhân, chia số thập phân và các bài toán tỉ số phần trăm thực tế.",
                "lessons": [
                    {
                        "id": "b4",
                        "title": "Bài 4: Phép cộng và phép trừ số thập phân",
                        "badge": "Tính toán",
                        "questions_count": 25,
                    },
                    {"id": "b5", "title": "Bài 5: Phép nhân số thập phân", "badge": "Tính toán", "questions_count": 25},
                    {"id": "b6", "title": "Bài 6: Phép chia số thập phân", "badge": "Tính toán", "questions_count": 25},
                    {
                        "id": "b7",
                        "title": "Bài 7: Giải toán về tỉ số phần trăm",
                        "badge": "Toán có lời văn",
                        "questions_count": 25,
                    },
                    {
                        "id": "b-ot2",
                        "title": "Ôn tập & Đề kiểm tra Chương 2 Toán 5",
                        "badge": "Đề tự luyện",
                        "is_exam": True,
                        "questions_count": 35,
                    },
                ],
            },
            {
                "id": "c3",
                "title": "Chương 3: Hình học - Diện tích & Thể tích",
                "desc": "Hình tam giác, hình thang, hình tròn, hình hộp chữ nhật, hình lập phương.",
                "lessons": [
                    {
                        "id": "b8",
                        "title": "Bài 8: Diện tích hình tam giác và hình thang",
                        "badge": "Hình học",
                        "questions_count": 25,
                    },
                    {
                        "id": "b9",
                        "title": "Bài 9: Chu vi và diện tích hình tròn",
                        "badge": "Hình tròn",
                        "questions_count": 25,
                    },
                    {
                        "id": "b10",
                        "title": "Bài 10: Diện tích xung quanh và toàn phần hình hộp chữ nhật, hình lập phương",
                        "badge": "Hình không gian",
                        "questions_count": 25,
                    },
                    {
                        "id": "b11",
                        "title": "Bài 11: Thể tích hình hộp chữ nhật và hình lập phương (cm3, dm3, m3)",
                        "badge": "Thể tích",
                        "questions_count": 25,
                    },
                    {
                        "id": "b-ot3",
                        "title": "Ôn tập & Đề kiểm tra Chương 3 Toán 5",
                        "badge": "Đề tự luyện",
                        "is_exam": True,
                        "questions_count": 35,
                    },
                ],
            },
            {
                "id": "c4",
                "title": "Chương 4: Toán chuyển động đều & Ôn thi vào Lớp 6",
                "desc": "Vận tốc, quãng đường, thời gian, chuyển động ngược chiều, cùng chiều và tuyển sinh vào lớp 6.",
                "lessons": [
                    {
                        "id": "b12",
                        "title": "Bài 12: Vận tốc (v = s : t)",
                        "badge": "Công thức cốt lõi",
                        "questions_count": 20,
                    },
                    {
                        "id": "b13",
                        "title": "Bài 13: Quãng đường (s = v x t) và Thời gian (t = s : v)",
                        "badge": "Tính toán",
                        "questions_count": 25,
                    },
                    {
                        "id": "b14",
                        "title": "Bài 14: Bài toán hai chuyển động cùng chiều và ngược chiều",
                        "badge": "Nâng cao",
                        "questions_count": 25,
                    },
                    {
                        "id": "b15",
                        "title": "Bài 15: Tuyển tập đề thi khảo sát tuyển sinh vào Lớp 6 CLC",
                        "badge": "Khảo sát vào 6",
                        "is_exam": True,
                        "questions_count": 40,
                    },
                ],
            },
        ],
    },
    # Lớp 11
    "lop-11_vat-li-11": {
        "subject_name": "Vật Lí Lớp 11",
        "grade_name": "Lớp 11",
        "grade_id": "lop-11",
        "icon": "⚡",
        "color": "#0284c7",
        "description": "Chương trình Vật lí 11 theo chuẩn SGK mới (Kết nối tri thức, Cánh diều, Chân trời sáng tạo).",
        "db_root_id": 2,  # Matches Category ID 2 in DB (Vật lí 11)
        "chapters": [
            {
                "id": "c1",
                "title": "Chương 1: Dao động",
                "desc": "Dao động cơ, dao động điều hòa, các đại lượng đặc trưng và năng lượng dao động.",
                "db_cat_id": 4,
                "lessons": [
                    {
                        "id": "b1",
                        "title": "Bài 1: Dao động điều hòa",
                        "badge": "Lý thuyết & Trắc nghiệm",
                        "db_cat_id": 8,
                        "questions_count": 25,
                    },
                    {
                        "id": "b2",
                        "title": "Bài 2: Mô tả dao động điều hòa",
                        "badge": "Lý thuyết & Trắc nghiệm",
                        "db_cat_id": 9,
                        "questions_count": 20,
                    },
                    {
                        "id": "b3",
                        "title": "Bài 3: Vận tốc, gia tốc trong dao động điều hòa",
                        "badge": "Bài tập trọng tâm",
                        "db_cat_id": 10,
                        "questions_count": 30,
                    },
                    {
                        "id": "b4",
                        "title": "Bài 4: Năng lượng trong dao động điều hòa",
                        "badge": "Công thức & Bài tập",
                        "questions_count": 20,
                    },
                    {
                        "id": "b5",
                        "title": "Bài 5: Dao động tắt dần, dao động cưỡng bức và hiện tượng cộng hưởng",
                        "badge": "Ứng dụng thực tế",
                        "questions_count": 15,
                    },
                    {
                        "id": "b-ot1",
                        "title": "Ôn tập & Đề thi tự luyện Chương 1: Dao động",
                        "badge": "Đề kiểm tra",
                        "is_exam": True,
                        "questions_count": 40,
                    },
                ],
            },
            {
                "id": "c2",
                "title": "Chương 2: Sóng",
                "desc": "Quá trình truyền sóng, các đặc trưng hình học của sóng, giao thoa và sóng dừng.",
                "db_cat_id": 5,
                "lessons": [
                    {
                        "id": "b8",
                        "title": "Bài 8: Mô tả sóng",
                        "badge": "Lý thuyết cơ bản",
                        "db_cat_id": 11,
                        "questions_count": 20,
                    },
                    {
                        "id": "b9",
                        "title": "Bài 9: Sóng ngang. Sóng dọc. Sự truyền năng lượng sóng",
                        "badge": "Lý thuyết & Trắc nghiệm",
                        "db_cat_id": 12,
                        "questions_count": 25,
                    },
                    {
                        "id": "b10",
                        "title": "Bài 10: Thực hành: Đo tần số của sóng âm",
                        "badge": "Thí nghiệm",
                        "questions_count": 15,
                    },
                    {"id": "b11", "title": "Bài 11: Sóng điện từ", "badge": "Trọng tâm", "questions_count": 25},
                    {
                        "id": "b12",
                        "title": "Bài 12: Giao thoa sóng",
                        "badge": "Bài toán cực đại - cực tiểu",
                        "questions_count": 30,
                    },
                    {"id": "b13", "title": "Bài 13: Sóng dừng", "badge": "Điều kiện sóng dừng", "questions_count": 25},
                    {
                        "id": "b-ot2",
                        "title": "Ôn tập & Đề kiểm tra Chương 2: Sóng",
                        "badge": "Đề kiểm tra",
                        "is_exam": True,
                        "questions_count": 40,
                    },
                ],
            },
            {
                "id": "c3",
                "title": "Chương 3: Điện trường",
                "desc": "Tương tác tĩnh điện, cường độ điện trường, điện thế, hiệu điện thế và tụ điện.",
                "lessons": [
                    {
                        "id": "b14",
                        "title": "Bài 14: Lực tương tác giữa các điện tích",
                        "badge": "Định luật Coulomb",
                        "questions_count": 20,
                    },
                    {
                        "id": "b15",
                        "title": "Bài 15: Khái niệm điện trường",
                        "badge": "Đường sức điện",
                        "questions_count": 20,
                    },
                    {
                        "id": "b16",
                        "title": "Bài 16: Điện thế và thế năng điện",
                        "badge": "Công của lực điện",
                        "questions_count": 25,
                    },
                    {
                        "id": "b17",
                        "title": "Bài 17: Tụ điện và năng lượng điện trường",
                        "badge": "Ghép tụ điện",
                        "questions_count": 25,
                    },
                    {
                        "id": "b-ot3",
                        "title": "Ôn tập & Đề kiểm tra Chương 3: Điện trường",
                        "badge": "Đề kiểm tra",
                        "is_exam": True,
                        "questions_count": 40,
                    },
                ],
            },
            {
                "id": "c4",
                "title": "Chương 4: Dòng điện không đổi & Mạch điện",
                "desc": "Cường độ dòng điện, định luật Ohm toàn mạch, công và công suất điện.",
                "lessons": [
                    {
                        "id": "b18",
                        "title": "Bài 18: Nguồn điện và suất điện động",
                        "badge": "Khái niệm",
                        "questions_count": 20,
                    },
                    {
                        "id": "b19",
                        "title": "Bài 19: Năng lượng điện và công suất điện",
                        "badge": "Định luật Joule-Lenz",
                        "questions_count": 25,
                    },
                    {
                        "id": "b20",
                        "title": "Bài 20: Thực hành: Đo suất điện động và điện trở trong",
                        "badge": "Thí nghiệm",
                        "questions_count": 15,
                    },
                ],
            },
        ],
    },
    "lop-11_toan-11": {
        "subject_name": "Toán Lớp 11",
        "grade_name": "Lớp 11",
        "grade_id": "lop-11",
        "icon": "📐",
        "color": "#2563eb",
        "description": "Chương trình Toán 11 Đại số, Giải tích và Hình học không gian bám sát SGK mới.",
        "db_root_id": 1,  # Matches Category ID 1 in DB (Toán 11)
        "chapters": [
            {
                "id": "c1",
                "title": "Chương 1: Hàm số lượng giác và phương trình lượng giác",
                "desc": "Góc lượng giác, công thức biến đổi lượng giác, hàm số và giải phương trình lượng giác cơ bản.",
                "db_cat_id": 6,
                "lessons": [
                    {
                        "id": "b1",
                        "title": "Bài 1: Giá trị lượng giác của góc lượng giác",
                        "badge": "Lý thuyết & Trắc nghiệm",
                        "db_cat_id": 13,
                        "questions_count": 30,
                    },
                    {
                        "id": "b2",
                        "title": "Bài 2: Công thức lượng giác",
                        "badge": "Công thức cộng & nhân đôi",
                        "db_cat_id": 14,
                        "questions_count": 35,
                    },
                    {
                        "id": "b3",
                        "title": "Bài 3: Hàm số lượng giác",
                        "badge": "Đồ thị & Tập xác định",
                        "questions_count": 25,
                    },
                    {
                        "id": "b4",
                        "title": "Bài 4: Phương trình lượng giác cơ bản",
                        "badge": "Dạng bài trọng tâm",
                        "questions_count": 35,
                    },
                    {
                        "id": "b-ot1",
                        "title": "Ôn tập & Đề tự luyện Chương 1: Lượng giác",
                        "badge": "Đề kiểm tra",
                        "is_exam": True,
                        "questions_count": 40,
                    },
                ],
            },
            {
                "id": "c2",
                "title": "Chương 2: Dãy số. Cấp số cộng và cấp số nhân",
                "desc": "Khái niệm dãy số, công thức số hạng tổng quát, tính tổng n số hạng của cấp số cộng, cấp số nhân.",
                "lessons": [
                    {
                        "id": "b5",
                        "title": "Bài 5: Dãy số tăng, dãy số giảm và bị chặn",
                        "badge": "Lý thuyết",
                        "questions_count": 20,
                    },
                    {
                        "id": "b6",
                        "title": "Bài 6: Cấp số cộng",
                        "badge": "Công thức & Tính chất",
                        "questions_count": 30,
                    },
                    {
                        "id": "b7",
                        "title": "Bài 7: Cấp số nhân",
                        "badge": "Công thức & Tính chất",
                        "questions_count": 30,
                    },
                    {
                        "id": "b-ot2",
                        "title": "Ôn tập Chương 2: Dãy số & Cấp số",
                        "badge": "Đề kiểm tra",
                        "is_exam": True,
                        "questions_count": 35,
                    },
                ],
            },
            {
                "id": "c3",
                "title": "Chương 3: Các số đặc trưng đo xu thế trung tâm của mẫu số liệu ghép nhóm",
                "desc": "Thống kê hiện đại: số trung bình, trung vị, tứ phân vị và mốt của mẫu ghép nhóm.",
                "lessons": [
                    {
                        "id": "b8",
                        "title": "Bài 8: Mẫu số liệu ghép nhóm",
                        "badge": "Bảng tần số",
                        "questions_count": 20,
                    },
                    {
                        "id": "b9",
                        "title": "Bài 9: Các số đặc trưng đo xu thế trung tâm",
                        "badge": "Công thức tính",
                        "questions_count": 25,
                    },
                ],
            },
            {
                "id": "c4",
                "title": "Chương 4: Quan hệ song song trong không gian",
                "desc": "Đường thẳng và mặt phẳng trong không gian, quan hệ song song, phép chiếu song song.",
                "lessons": [
                    {
                        "id": "b10",
                        "title": "Bài 10: Điểm, đường thẳng và mặt phẳng trong không gian",
                        "badge": "Hình học không gian",
                        "questions_count": 25,
                    },
                    {
                        "id": "b11",
                        "title": "Bài 11: Hai đường thẳng song song",
                        "badge": "Chứng minh song song",
                        "questions_count": 25,
                    },
                    {
                        "id": "b12",
                        "title": "Bài 12: Đường thẳng và mặt phẳng song song",
                        "badge": "Định lý & Bài tập",
                        "questions_count": 30,
                    },
                    {
                        "id": "b13",
                        "title": "Bài 13: Hai mặt phẳng song song",
                        "badge": "Thiết diện & Ứng dụng",
                        "questions_count": 30,
                    },
                ],
            },
            {
                "id": "c5",
                "title": "Chương 5: Giới hạn. Hàm số liên tục",
                "desc": "Giới hạn của dãy số, giới hạn của hàm số và tính liên tục của hàm số.",
                "lessons": [
                    {
                        "id": "b14",
                        "title": "Bài 14: Giới hạn của dãy số",
                        "badge": "Phương pháp tính",
                        "questions_count": 30,
                    },
                    {
                        "id": "b15",
                        "title": "Bài 15: Giới hạn của hàm số",
                        "badge": "Các dạng vô định",
                        "questions_count": 35,
                    },
                    {
                        "id": "b16",
                        "title": "Bài 16: Hàm số liên tục",
                        "badge": "Định lý giá trị trung gian",
                        "questions_count": 25,
                    },
                ],
            },
        ],
    },
    # Lớp 12
    "lop-12_vat-li-12": {
        "subject_name": "Vật Lí Lớp 12",
        "grade_name": "Lớp 12",
        "grade_id": "lop-12",
        "icon": "⚡",
        "color": "#0284c7",
        "description": "Chương trình Vật lí 12 phục vụ ôn thi tốt nghiệp THPT và kiểm tra học kỳ.",
        "db_root_id": 3,
        "chapters": [
            {
                "id": "c1",
                "title": "Chương 1: Vật lí nhiệt",
                "desc": "Mô hình động học phân tử, nhiệt độ và thang nhiệt độ, nhiệt dung riêng, nhiệt hóa hơi.",
                "lessons": [
                    {
                        "id": "b1",
                        "title": "Bài 1: Cấu trúc của chất và mô hình động học phân tử",
                        "badge": "Lý thuyết",
                        "questions_count": 25,
                    },
                    {
                        "id": "b2",
                        "title": "Bài 2: Nội năng và định luật I của nhiệt động lực học",
                        "badge": "Công thức tính",
                        "questions_count": 30,
                    },
                    {
                        "id": "b3",
                        "title": "Bài 3: Nhiệt độ. Thang nhiệt độ - Nhiệt kế",
                        "badge": "Thang Kelvin & Celsius",
                        "questions_count": 20,
                    },
                    {
                        "id": "b4",
                        "title": "Bài 4: Nhiệt dung riêng, nhiệt nóng chảy riêng, nhiệt hóa hơi riêng",
                        "badge": "Bài toán truyền nhiệt",
                        "questions_count": 30,
                    },
                ],
            },
            {
                "id": "c2",
                "title": "Chương 2: Khí lí tưởng",
                "desc": "Các định luật chất khí (Boyle, Charles) và phương trình trạng thái khí lí tưởng Clapeyron - Mendeleev.",
                "db_cat_id": 7,
                "lessons": [
                    {
                        "id": "b5",
                        "title": "Bài 5: Định luật Boyle. Định luật Charles",
                        "badge": "Đẳng nhiệt & Đẳng áp",
                        "questions_count": 30,
                    },
                    {
                        "id": "b6",
                        "title": "Bài 6: Phương trình trạng thái của khí lí tưởng",
                        "badge": "Trọng tâm TNTHPT",
                        "questions_count": 35,
                    },
                    {
                        "id": "b7",
                        "title": "Bài 7: Áp suất khí theo mô hình động học phân tử",
                        "badge": "Động năng phân tử",
                        "questions_count": 25,
                    },
                    {
                        "id": "b-ot2",
                        "title": "Ôn tập & Đề thi tự luyện Chương 2: Khí lí tưởng",
                        "badge": "Đề thi tự luận + trắc nghiệm",
                        "is_exam": True,
                        "questions_count": 40,
                    },
                ],
            },
            {
                "id": "c3",
                "title": "Chương 3: Từ trường",
                "desc": "Cảm ứng từ, lực từ tác dụng lên đoạn dây dẫn mang dòng điện và cảm ứng điện từ.",
                "lessons": [
                    {
                        "id": "b8",
                        "title": "Bài 8: Từ trường và đường sức từ",
                        "badge": "Quy tắc bàn tay phải",
                        "questions_count": 25,
                    },
                    {
                        "id": "b9",
                        "title": "Bài 9: Lực từ tác dụng lên dòng điện",
                        "badge": "Quy tắc bàn tay trái",
                        "questions_count": 30,
                    },
                    {
                        "id": "b10",
                        "title": "Bài 10: Cảm ứng điện từ. Định luật Faraday",
                        "badge": "Suất điện động cảm ứng",
                        "questions_count": 35,
                    },
                ],
            },
            {
                "id": "c4",
                "title": "Chương 4: Vật lí hạt nhân",
                "desc": "Cấu tạo hạt nhân, năng lượng liên kết, hiện tượng phóng xạ và phản ứng hạt nhân.",
                "lessons": [
                    {
                        "id": "b11",
                        "title": "Bài 11: Cấu trúc hạt nhân và năng lượng liên kết",
                        "badge": "Độ hụt khối",
                        "questions_count": 30,
                    },
                    {
                        "id": "b12",
                        "title": "Bài 12: Hiện tượng phóng xạ",
                        "badge": "Định luật phóng xạ & Chu kì bán rã",
                        "questions_count": 35,
                    },
                    {
                        "id": "b13",
                        "title": "Bài 13: Phản ứng phân hạch và nhiệt hạch",
                        "badge": "Năng lượng hạt nhân",
                        "questions_count": 25,
                    },
                ],
            },
        ],
    },
    "lop-12_toan-12": {
        "subject_name": "Toán Lớp 12",
        "grade_name": "Lớp 12",
        "grade_id": "lop-12",
        "icon": "📐",
        "color": "#2563eb",
        "description": "Ứng dụng đạo hàm khảo sát hàm số, nguyên hàm tích phân, hình học Oxyz và xác suất có điều kiện.",
        "chapters": [
            {
                "id": "c1",
                "title": "Chương 1: Ứng dụng đạo hàm để khảo sát và vẽ đồ thị hàm số",
                "desc": "Tính đơn điệu, cực trị, giá trị lớn nhất - nhỏ nhất, đường tiệm cận và đồ thị hàm số.",
                "lessons": [
                    {
                        "id": "b1",
                        "title": "Bài 1: Tính đơn điệu của hàm số",
                        "badge": "Đồng biến & Nghịch biến",
                        "questions_count": 35,
                    },
                    {
                        "id": "b2",
                        "title": "Bài 2: Cực trị của hàm số",
                        "badge": "Cực đại & Cực tiểu",
                        "questions_count": 35,
                    },
                    {
                        "id": "b3",
                        "title": "Bài 3: Giá trị lớn nhất và nhỏ nhất của hàm số",
                        "badge": "Min - Max",
                        "questions_count": 30,
                    },
                    {
                        "id": "b4",
                        "title": "Bài 4: Đường tiệm cận của đồ thị hàm số",
                        "badge": "Tiệm cận đứng, ngang, xiên",
                        "questions_count": 30,
                    },
                    {
                        "id": "b5",
                        "title": "Bài 5: Khảo sát sự biến thiên và vẽ đồ thị của một số hàm đa thức & phân thức",
                        "badge": "Trọng tâm TNTHPT",
                        "questions_count": 40,
                    },
                ],
            },
            {
                "id": "c2",
                "title": "Chương 2: Toạ độ của vectơ trong không gian Oxyz",
                "desc": "Hệ toạ độ không gian, các phép toán vectơ, biểu thức toạ độ của tích vô hướng.",
                "lessons": [
                    {
                        "id": "b6",
                        "title": "Bài 6: Vectơ và các phép toán vectơ trong không gian",
                        "badge": "Quy tắc hình hộp",
                        "questions_count": 30,
                    },
                    {
                        "id": "b7",
                        "title": "Bài 7: Hệ trục toạ độ trong không gian Oxyz",
                        "badge": "Toạ độ điểm & vectơ",
                        "questions_count": 35,
                    },
                    {
                        "id": "b8",
                        "title": "Bài 8: Biểu thức toạ độ của tích vô hướng",
                        "badge": "Góc & Khoảng cách",
                        "questions_count": 35,
                    },
                ],
            },
            {
                "id": "c3",
                "title": "Chương 3: Các số đặc trưng đo mức độ phân tán của mẫu số liệu ghép nhóm",
                "desc": "Khoảng biến thiên, khoảng tứ phân vị, phương sai và độ lệch chuẩn của mẫu số liệu ghép nhóm.",
                "lessons": [
                    {
                        "id": "b9",
                        "title": "Bài 9: Khoảng biến thiên và khoảng tứ phân vị",
                        "badge": "Thống kê",
                        "questions_count": 25,
                    },
                    {
                        "id": "b10",
                        "title": "Bài 10: Phương sai và độ lệch chuẩn",
                        "badge": "Công thức tính",
                        "questions_count": 25,
                    },
                ],
            },
            {
                "id": "c4",
                "title": "Chương 4: Nguyên hàm và tích phân",
                "desc": "Khái niệm nguyên hàm, bảng nguyên hàm cơ bản, phương pháp tính tích phân và ứng dụng hình học.",
                "lessons": [
                    {"id": "b11", "title": "Bài 11: Nguyên hàm", "badge": "Bảng nguyên hàm", "questions_count": 40},
                    {"id": "b12", "title": "Bài 12: Tích phân", "badge": "Đổi biến & Từng phần", "questions_count": 40},
                    {
                        "id": "b13",
                        "title": "Bài 13: Ứng dụng tích phân tính diện tích hình phẳng và thể tích",
                        "badge": "Bài toán thực tế",
                        "questions_count": 35,
                    },
                ],
            },
        ],
    },
    # Đại học
    "dai-hoc_triet-hoc": {
        "subject_name": "Triết Học Mác - Lênin",
        "grade_name": "Đại học",
        "grade_id": "dai-hoc",
        "icon": "🏛️",
        "color": "#7c3aed",
        "description": "Học phần đại cương bắt buộc dành cho sinh viên tất cả các trường Đại học và Cao đẳng.",
        "chapters": [
            {
                "id": "c1",
                "title": "Chương 1: Triết học và vai trò của triết học trong đời sống xã hội",
                "desc": "Khái lược về triết học, vấn đề cơ bản của triết học, sự hình thành và phát triển của Triết học Mác - Lênin.",
                "lessons": [
                    {
                        "id": "b1",
                        "title": "Bài 1: Khái lược về triết học và vấn đề cơ bản của triết học",
                        "badge": "Chủ nghĩa duy vật & duy tâm",
                        "questions_count": 30,
                    },
                    {
                        "id": "b2",
                        "title": "Bài 2: Sự ra đời và các giai đoạn phát triển của Triết học Mác - Lênin",
                        "badge": "Tiền đề kinh tế & lý luận",
                        "questions_count": 25,
                    },
                    {
                        "id": "b3",
                        "title": "Bài 3: Đối tượng và chức năng của Triết học Mác - Lênin",
                        "badge": "Thế giới quan & Phương pháp luận",
                        "questions_count": 20,
                    },
                ],
            },
            {
                "id": "c2",
                "title": "Chương 2: Chủ nghĩa duy vật biện chứng",
                "desc": "Vật chất và ý thức, hai nguyên lý, ba quy luật và sáu cặp phạm trù của phép biện chứng duy vật.",
                "lessons": [
                    {
                        "id": "b4",
                        "title": "Bài 4: Vật chất và phương thức tồn tại của vật chất",
                        "badge": "Định nghĩa vật chất của Lênin",
                        "questions_count": 35,
                    },
                    {
                        "id": "b5",
                        "title": "Bài 5: Nguồn gốc, bản chất và kết cấu của ý thức",
                        "badge": "Mối quan hệ vật chất & ý thức",
                        "questions_count": 30,
                    },
                    {
                        "id": "b6",
                        "title": "Bài 6: Hai nguyên lý cơ bản của phép biện chứng duy vật",
                        "badge": "Mối liên hệ phổ biến & Sự phát triển",
                        "questions_count": 30,
                    },
                    {
                        "id": "b7",
                        "title": "Bài 7: Ba quy luật cơ bản của phép biện chứng duy vật",
                        "badge": "Lượng - Chất, Mâu thuẫn, Phủ định của phủ định",
                        "questions_count": 40,
                    },
                    {
                        "id": "b8",
                        "title": "Bài 8: Sáu cặp phạm trù cơ bản của phép biện chứng duy vật",
                        "badge": "Cái riêng - cái chung, Bản chất - hiện tượng",
                        "questions_count": 35,
                    },
                    {
                        "id": "b9",
                        "title": "Bài 9: Lý luận nhận thức duy vật biện chứng",
                        "badge": "Thực tiễn & Chân lý",
                        "questions_count": 30,
                    },
                ],
            },
            {
                "id": "c3",
                "title": "Chương 3: Chủ nghĩa duy vật lịch sử",
                "desc": "Học thuyết hình thái kinh tế - xã hội, giai cấp và dân tộc, nhà nước và cách mạng xã hội, ý thức xã hội.",
                "lessons": [
                    {
                        "id": "b10",
                        "title": "Bài 10: Quy luật quan hệ sản xuất phù hợp với trình độ phát triển của lực lượng sản xuất",
                        "badge": "Cơ sở hạ tầng & Kiến trúc thượng tầng",
                        "questions_count": 35,
                    },
                    {
                        "id": "b11",
                        "title": "Bài 11: Tồn tại xã hội và ý thức xã hội",
                        "badge": "Tính độc lập tương đối của ý thức xã hội",
                        "questions_count": 30,
                    },
                    {
                        "id": "b12",
                        "title": "Bài 12: Con người và vai trò của quần chúng nhân dân trong lịch sử",
                        "badge": "Quần chúng nhân dân & Lãnh tụ",
                        "questions_count": 25,
                    },
                ],
            },
        ],
    },
    # ==================== CẤU TRÚC CHUYÊN BIỆT TIẾNG ANH: TOEIC ====================
    "tieng-anh_toeic": {
        "is_language": True,
        "subject_slug": "toeic",
        "subject_name": "Luyện thi TOEIC (Format ETS)",
        "grade_name": "Tiếng Anh",
        "grade_id": "tieng-anh",
        "icon": "🎧",
        "color": "#0284c7",
        "description": "Chương trình luyện thi TOEIC format ETS: Bài test năng lực đầu vào, phần Study bài giảng 4 kỹ năng và phần Practice luyện đề trắc nghiệm có bấm giờ.",
        "placement_test": {
            "title": "Bài Test Đánh Giá Năng Lực TOEIC Đầu Vào (Format ETS)",
            "desc": "Bài test 45 phút chuẩn đoán bao quát 4 kỹ năng Listening, Reading, Writing, Speaking giúp xác định chính xác thang điểm hiện tại (10 - 990) và đề xuất lộ trình ôn luyện tối ưu.",
            "questions_count": 50,
            "duration_minutes": 45,
            "skills": ["Listening", "Reading", "Writing", "Speaking"],
            "badge": "Test Năng Lực Đầu Vào",
        },
        "study_skills": [
            {
                "id": "listening",
                "name": "Listening (Kỹ năng Nghe)",
                "icon": "🎧",
                "desc": "Phương pháp luyện nghe bắt từ khóa, phân tích bẫy phát âm và phản xạ hội thoại Part 1 - Part 4",
                "lessons": [
                    {
                        "title": "Unit 1: Mô tả tranh (Part 1) & Kỹ thuật nhận diện bẫy thì, bẫy vị trí",
                        "badge": "Photographs",
                        "duration": "30 phút",
                        "topics": "Audio mẹo Part 1",
                    },
                    {
                        "title": "Unit 2: Hỏi & Đáp (Part 2) - Bắt nhanh Wh-questions và câu trả lời gián tiếp",
                        "badge": "Q&A Tricks",
                        "duration": "45 phút",
                        "topics": "60 Mẫu câu hỏi đáp",
                    },
                    {
                        "title": "Unit 3: Đoạn hội thoại (Part 3) - Phân tích câu hỏi suy luận & Ngụ ý",
                        "badge": "Conversations",
                        "duration": "45 phút",
                        "topics": "Nghe 3 người đối thoại",
                    },
                    {
                        "title": "Unit 4: Bài nói chuyện (Part 4) - Độc thoại thông báo, tin tức và thuyết trình",
                        "badge": "Short Talks",
                        "duration": "40 phút",
                        "topics": "Chiến lược Skimming",
                    },
                ],
            },
            {
                "id": "reading",
                "name": "Reading (Kỹ năng Đọc)",
                "icon": "📖",
                "desc": "Trọng điểm ngữ pháp Part 5, điền từ vào đoạn văn Part 6 và kỹ thuật đọc hiểu văn bản Part 7",
                "lessons": [
                    {
                        "title": "Unit 1: 10 Dạng từ loại & Ngữ pháp trọng điểm cốt lõi Part 5",
                        "badge": "Grammar Core",
                        "duration": "45 phút",
                        "topics": "Quy tắc vị trí từ loại",
                    },
                    {
                        "title": "Unit 2: Hoàn thành đoạn văn (Part 6) - Phương pháp điền câu & Liên từ liên kết",
                        "badge": "Text Completion",
                        "duration": "35 phút",
                        "topics": "Liên kết ngữ cảnh",
                    },
                    {
                        "title": "Unit 3: Đọc hiểu đoạn đơn (Part 7) - Kỹ thuật Skimming & Scanning văn bản thực tế",
                        "badge": "Single Passage",
                        "duration": "50 phút",
                        "topics": "Email, hóa đơn, bài báo",
                    },
                    {
                        "title": "Unit 4: Đọc hiểu đoạn kép & đoạn ba (Part 7) - Xử lý câu hỏi xâu chuỗi thông tin",
                        "badge": "Multiple Passages",
                        "duration": "50 phút",
                        "topics": "So sánh thông tin",
                    },
                ],
            },
            {
                "id": "writing",
                "name": "Writing (Kỹ năng Viết)",
                "icon": "✍️",
                "desc": "Cấu trúc viết câu mô tả tranh, viết Email phản hồi công việc và bài luận nêu quan điểm",
                "lessons": [
                    {
                        "title": "Unit 1: Kỹ thuật viết câu chuẩn ngữ pháp dựa trên 2 từ cho trước (Q1 - Q5)",
                        "badge": "Sentence Building",
                        "duration": "40 phút",
                        "topics": "Ngữ pháp câu phức",
                    },
                    {
                        "title": "Unit 2: Mẫu viết Email hỏi đáp, yêu cầu thông tin và khiếu nại (Q6 - Q7)",
                        "badge": "Business Email",
                        "duration": "45 phút",
                        "topics": "Barem chấm điểm ETS",
                    },
                    {
                        "title": "Unit 3: Cấu trúc 4 đoạn cho bài luận nghị luận quan điểm cá nhân (Q8)",
                        "badge": "Opinion Essay",
                        "duration": "60 phút",
                        "topics": "Dàn ý chuẩn Band cao",
                    },
                ],
            },
            {
                "id": "speaking",
                "name": "Speaking (Kỹ năng Nói)",
                "icon": "🗣️",
                "desc": "Chuẩn hóa phát âm IPA, ngữ điệu đọc đoạn văn, mô tả tranh và phản xạ giao tiếp công sở",
                "lessons": [
                    {
                        "title": "Unit 1: Đọc to đoạn văn với phát âm chuẩn, trọng âm từ và ngữ điệu câu",
                        "badge": "Read Aloud",
                        "duration": "30 phút",
                        "topics": "Ngữ âm & Nối âm",
                    },
                    {
                        "title": "Unit 2: Kỹ năng mô tả tranh chi tiết trong 45 giây (Bối cảnh, hành động, vị trí)",
                        "badge": "Describe a Picture",
                        "duration": "35 phút",
                        "topics": "Từ vựng miêu tả",
                    },
                    {
                        "title": "Unit 3: Trả lời câu hỏi phỏng vấn & Xử lý tình huống thông tin cho sẵn",
                        "badge": "Respond to Questions",
                        "duration": "40 phút",
                        "topics": "Phản xạ nhanh",
                    },
                    {
                        "title": "Unit 4: Đề xuất giải pháp và trình bày quan điểm thuyết phục",
                        "badge": "Express Opinion",
                        "duration": "45 phút",
                        "topics": "Công thức AREA",
                    },
                ],
            },
        ],
        "practice_skills": [
            {
                "id": "listening_practice",
                "name": "Listening Practice (Luyện đề Nghe)",
                "icon": "🎧",
                "desc": "Ngân hàng bài tập nghe trắc nghiệm có audio, transcript và lời giải chi tiết",
                "exercises": [
                    {
                        "title": "Luyện đề Part 1: 50 Câu hỏi mô tả tranh chọn lọc format ETS",
                        "questions_count": 50,
                        "duration": "25 phút",
                        "badge": "Part 1 Practice",
                    },
                    {
                        "title": "Luyện đề Part 2: 60 Câu hỏi đáp né bẫy phát âm & từ đồng âm",
                        "questions_count": 60,
                        "duration": "30 phút",
                        "badge": "Part 2 Practice",
                    },
                    {
                        "title": "Luyện đề Part 3: 15 Đoạn hội thoại đa chủ đề có giải thích bẫy",
                        "questions_count": 45,
                        "duration": "35 phút",
                        "badge": "Part 3 Practice",
                    },
                    {
                        "title": "Luyện đề Part 4: 10 Bài nói chuyện độc thoại có biểu đồ kèm theo",
                        "questions_count": 30,
                        "duration": "25 phút",
                        "badge": "Part 4 Practice",
                    },
                    {
                        "title": "Đề thi thử TOEIC Listening Full 100 câu (Format ETS mới nhất)",
                        "questions_count": 100,
                        "duration": "45 phút",
                        "badge": "Full Listening",
                        "highlight": True,
                    },
                ],
            },
            {
                "id": "reading_practice",
                "name": "Reading Practice (Luyện đề Đọc)",
                "icon": "📖",
                "desc": "Kho đề thi trắc nghiệm đọc hiểu bấm giờ chấm điểm tự động",
                "exercises": [
                    {
                        "title": "Luyện đề Part 5: 100 Câu trắc nghiệm ngữ pháp & từ vựng chọn lọc",
                        "questions_count": 100,
                        "duration": "40 phút",
                        "badge": "Part 5 Practice",
                    },
                    {
                        "title": "Luyện đề Part 6: 40 Câu điền từ hoàn thành đoạn văn công sở",
                        "questions_count": 40,
                        "duration": "30 phút",
                        "badge": "Part 6 Practice",
                    },
                    {
                        "title": "Luyện đề Part 7: 54 Câu đọc hiểu đoạn đơn, đoạn kép, đoạn ba",
                        "questions_count": 54,
                        "duration": "55 phút",
                        "badge": "Part 7 Practice",
                    },
                    {
                        "title": "Đề thi thử TOEIC Reading Full 100 câu (Chuẩn cấu trúc ETS)",
                        "questions_count": 100,
                        "duration": "75 phút",
                        "badge": "Full Reading",
                        "highlight": True,
                    },
                ],
            },
            {
                "id": "writing_practice",
                "name": "Writing Practice (Luyện đề Viết)",
                "icon": "✍️",
                "desc": "Đề bài luyện viết thực tế kèm bài mẫu tham khảo điểm tối đa",
                "exercises": [
                    {
                        "title": "Luyện viết câu miêu tả 20 bức tranh thực tế (Kèm câu mẫu)",
                        "questions_count": 20,
                        "duration": "30 phút",
                        "badge": "Sentence Practice",
                    },
                    {
                        "title": "Thực hành phản hồi 5 tình huống Email công việc thực tế",
                        "questions_count": 5,
                        "duration": "40 phút",
                        "badge": "Email Practice",
                    },
                    {
                        "title": "Đề luyện viết bài luận hoàn chỉnh kèm Sample Band 200/200",
                        "questions_count": 3,
                        "duration": "60 phút",
                        "badge": "Essay Sample",
                        "highlight": True,
                    },
                ],
            },
            {
                "id": "speaking_practice",
                "name": "Speaking Practice (Luyện đề Nói)",
                "icon": "🗣️",
                "desc": "Phòng luyện nói tương tác có đồng hồ đếm ngược và câu trả lời mẫu",
                "exercises": [
                    {
                        "title": "Luyện đọc to 15 đoạn văn bản thông báo & quảng cáo",
                        "questions_count": 15,
                        "duration": "20 phút",
                        "badge": "Read Aloud",
                    },
                    {
                        "title": "Luyện mô tả 10 tranh với đồng hồ đếm ngược 45 giây",
                        "questions_count": 10,
                        "duration": "25 phút",
                        "badge": "Describe Picture",
                    },
                    {
                        "title": "Bộ 20 câu hỏi phỏng vấn phản xạ trực tiếp có audio mẫu",
                        "questions_count": 20,
                        "duration": "30 phút",
                        "badge": "Q&A Response",
                    },
                    {
                        "title": "Thi thử TOEIC Speaking Full Test (11 câu hỏi mô phỏng thi thật)",
                        "questions_count": 11,
                        "duration": "20 phút",
                        "badge": "Full Speaking Test",
                        "highlight": True,
                    },
                ],
            },
        ],
        "full_tests": [
            {
                "title": "Đề thi thử TOEIC Full Test 01 (Format ETS Mới Nhất - 200 Câu / 120 Phút)",
                "duration": "120 phút",
                "questions_count": 200,
                "badge": "Full Mock Test",
            },
            {
                "title": "Đề thi thử TOEIC Full Test 02 (Bản quyền đề thi thử có chấm điểm tự động)",
                "duration": "120 phút",
                "questions_count": 200,
                "badge": "Full Mock Test",
            },
        ],
        "chapters": [],
    },
    # ==================== CẤU TRÚC CHUYÊN BIỆT TIẾNG ANH: IELTS ====================
    "tieng-anh_ielts": {
        "is_language": True,
        "subject_name": "Luyện thi IELTS (Academic & General)",
        "grade_name": "Tiếng Anh",
        "grade_id": "tieng-anh",
        "icon": "💎",
        "color": "#4338ca",
        "description": "Lộ trình chinh phục IELTS Band 7.5+: Bài test năng lực IELTS, phần Study bài giảng chuyên sâu và phần Practice luyện đề 4 kỹ năng Listening, Reading, Writing, Speaking.",
        "placement_test": {
            "title": "Bài Test Đánh Giá Năng Lực IELTS Toàn Diện (Band Diagnostic)",
            "desc": "Bài kiểm tra chuẩn đoán 45 phút đánh giá toàn diện 4 kỹ năng để dự đoán thang điểm IELTS (Band 1.0 - 9.0) và phân bổ lộ trình ôn tập cá nhân hóa.",
            "questions_count": 45,
            "duration_minutes": 45,
            "skills": ["Listening", "Reading", "Writing", "Speaking"],
            "badge": "IELTS Diagnostic Test",
        },
        "study_skills": [
            {
                "id": "listening",
                "name": "Listening (Kỹ năng Nghe)",
                "icon": "🎧",
                "desc": "Chiến thuật nghe bắt từ khóa Section 1 đến Section 4, tránh bẫy chính tả và số từ cho phép",
                "lessons": [
                    {
                        "title": "Unit 1: Section 1 - Form/Note Completion (Bắt tên, số điện thoại, ngày tháng)",
                        "badge": "Form Completion",
                        "duration": "35 phút",
                    },
                    {
                        "title": "Unit 2: Section 2 - Map & Plan Labelling (Kỹ thuật định hướng bản đồ)",
                        "badge": "Map Labelling",
                        "duration": "40 phút",
                    },
                    {
                        "title": "Unit 3: Section 3 - Multiple Choice & Matching (Hội thoại học thuật 2-3 người)",
                        "badge": "Multiple Choice",
                        "duration": "45 phút",
                    },
                    {
                        "title": "Unit 4: Section 4 - Academic Lecture (Nghe bài giảng đại học không ngừng nghỉ)",
                        "badge": "Lecture Summary",
                        "duration": "45 phút",
                    },
                ],
            },
            {
                "id": "reading",
                "name": "Reading (Kỹ năng Đọc)",
                "icon": "📖",
                "desc": "Kỹ thuật Skimming/Scanning và phương pháp giải quyết 10 dạng câu hỏi IELTS Reading",
                "lessons": [
                    {
                        "title": "Unit 1: True / False / Not Given & Yes / No / Not Given phân biệt triệt để",
                        "badge": "T/F/NG Master",
                        "duration": "45 phút",
                    },
                    {
                        "title": "Unit 2: Matching Headings (Kỹ thuật tìm câu chủ đề và nối tiêu đề đoạn văn)",
                        "badge": "Headings Match",
                        "duration": "45 phút",
                    },
                    {
                        "title": "Unit 3: Summary Completion & Sentence Completion (Bắt từ khóa trong bài đọc)",
                        "badge": "Summary Fill",
                        "duration": "40 phút",
                    },
                    {
                        "title": "Unit 4: Matching Features & Information (Nối quan điểm chuyên gia và sự kiện)",
                        "badge": "Matching Features",
                        "duration": "40 phút",
                    },
                ],
            },
            {
                "id": "writing",
                "name": "Writing (Kỹ năng Viết)",
                "icon": "✍️",
                "desc": "Phương pháp viết Task 1 mô tả biểu đồ và Task 2 bài luận nghị luận xã hội",
                "lessons": [
                    {
                        "title": "Unit 1: Task 1 - Biểu đồ xu hướng (Line Graph, Bar Chart) & Từ vựng thay đổi",
                        "badge": "Task 1 Trends",
                        "duration": "50 phút",
                    },
                    {
                        "title": "Unit 2: Task 1 - Biểu đồ so sánh (Pie Chart, Table) và Bản đồ / Quy trình (Map/Process)",
                        "badge": "Task 1 Process",
                        "duration": "50 phút",
                    },
                    {
                        "title": "Unit 3: Task 2 - Cấu trúc 4 đoạn mở bài, thân bài, kết luận cho bài luận Argument",
                        "badge": "Task 2 Essay",
                        "duration": "60 phút",
                    },
                    {
                        "title": "Unit 4: Nâng cấp Lexical Resource & Cohesion: Sử dụng Collocations và từ nối",
                        "badge": "Band 7.0+ Vocab",
                        "duration": "45 phút",
                    },
                ],
            },
            {
                "id": "speaking",
                "name": "Speaking (Kỹ năng Nói)",
                "icon": "🗣️",
                "desc": "Chiến thuật trả lời Part 1 cá nhân, Part 2 độc thoại 2 phút và Part 3 thảo luận chuyên sâu",
                "lessons": [
                    {
                        "title": "Unit 1: Part 1 - Chiến thuật trả lời câu hỏi quen thuộc theo công thức 2-3 câu",
                        "badge": "Part 1 Fluency",
                        "duration": "35 phút",
                    },
                    {
                        "title": "Unit 2: Part 2 - Kỹ thuật lập dàn ý trong 1 phút và nói liên tục 2 phút",
                        "badge": "Part 2 Cue Card",
                        "duration": "45 phút",
                    },
                    {
                        "title": "Unit 3: Part 3 - Thảo luận trừu tượng, so sánh quá khứ/tương lai, phân tích 2 mặt",
                        "badge": "Part 3 Deep Talk",
                        "duration": "45 phút",
                    },
                    {
                        "title": "Unit 4: Pronunciation & Lexical Resource: Ngữ điệu tự nhiên và Idioms đắt giá",
                        "badge": "Speaking Band 8",
                        "duration": "40 phút",
                    },
                ],
            },
        ],
        "practice_skills": [
            {
                "id": "listening_practice",
                "name": "Listening Practice (Luyện đề Nghe)",
                "icon": "🎧",
                "desc": "Kho đề luyện nghe IELTS trích từ bộ đề Cambridge có audio và đáp án",
                "exercises": [
                    {
                        "title": "Luyện nghe Section 1: 10 Bài điền thông tin form đặt dịch vụ",
                        "questions_count": 40,
                        "duration": "30 phút",
                        "badge": "Section 1 Set",
                    },
                    {
                        "title": "Luyện nghe Section 2: 8 Bài bản đồ định vị khu bảo tồn, trường học",
                        "questions_count": 35,
                        "duration": "30 phút",
                        "badge": "Section 2 Set",
                    },
                    {
                        "title": "Luyện nghe Section 3 & 4: 10 Bài thảo luận nghiên cứu khoa học",
                        "questions_count": 40,
                        "duration": "35 phút",
                        "badge": "Section 3-4 Set",
                    },
                    {
                        "title": "Đề thi thử IELTS Listening Full Test (40 câu - 40 phút)",
                        "questions_count": 40,
                        "duration": "40 phút",
                        "badge": "Full Listening",
                        "highlight": True,
                    },
                ],
            },
            {
                "id": "reading_practice",
                "name": "Reading Practice (Luyện đề Đọc)",
                "icon": "📖",
                "desc": "Tuyển tập các bài đọc Academic chuẩn Cambridge bấm giờ chấm điểm",
                "exercises": [
                    {
                        "title": "Chuyên đề luyện đọc: 20 Bài đọc True / False / Not Given",
                        "questions_count": 40,
                        "duration": "45 phút",
                        "badge": "T/F/NG Practice",
                    },
                    {
                        "title": "Chuyên đề luyện đọc: 15 Bài đọc nối tiêu đề Matching Headings",
                        "questions_count": 35,
                        "duration": "45 phút",
                        "badge": "Headings Practice",
                    },
                    {
                        "title": "Bộ đề thi thử IELTS Reading Full Passage (3 Bài đọc - 40 câu / 60 phút)",
                        "questions_count": 40,
                        "duration": "60 phút",
                        "badge": "Full Reading",
                        "highlight": True,
                    },
                ],
            },
            {
                "id": "writing_practice",
                "name": "Writing Practice (Luyện đề Viết)",
                "icon": "✍️",
                "desc": "Kho đề thi viết cập nhật theo quý có bài mẫu Sample Band 8.0+",
                "exercises": [
                    {
                        "title": "Thực hành viết 10 đề Task 1 Biểu đồ xu hướng (Kèm Sample 8.0)",
                        "questions_count": 10,
                        "duration": "40 phút",
                        "badge": "Task 1 Practice",
                    },
                    {
                        "title": "Thực hành viết 5 đề Task 1 Bản đồ và Quy trình sản xuất",
                        "questions_count": 5,
                        "duration": "40 phút",
                        "badge": "Task 1 Process",
                    },
                    {
                        "title": "Thực hành viết 10 đề Task 2 các chủ đề nóng: AI, Môi trường, Giáo dục",
                        "questions_count": 10,
                        "duration": "60 phút",
                        "badge": "Task 2 Practice",
                        "highlight": True,
                    },
                ],
            },
            {
                "id": "speaking_practice",
                "name": "Speaking Practice (Luyện đề Nói)",
                "icon": "🗣️",
                "desc": "Ngân hàng câu hỏi Speaking cập nhật theo quý có gợi ý ý tưởng",
                "exercises": [
                    {
                        "title": "Bộ đề dự đoán Speaking Part 1 (30 Chủ đề thường gặp nhất)",
                        "questions_count": 30,
                        "duration": "25 phút",
                        "badge": "Part 1 Bank",
                    },
                    {
                        "title": "Bộ đề dự đoán Speaking Part 2 (50 Cue Cards kèm dàn ý mẫu)",
                        "questions_count": 20,
                        "duration": "40 phút",
                        "badge": "Part 2 Bank",
                    },
                    {
                        "title": "Luyện phản xạ Speaking Part 3: Câu hỏi thảo luận chuyên sâu",
                        "questions_count": 25,
                        "duration": "35 phút",
                        "badge": "Part 3 Bank",
                    },
                ],
            },
        ],
        "full_tests": [
            {
                "title": "Đề thi thử IELTS Full Mock Test 01 (Academic 4 Kỹ Năng)",
                "duration": "165 phút",
                "questions_count": 82,
                "badge": "Full Academic Test",
            },
            {
                "title": "Đề thi thử IELTS Full Mock Test 02 (Chuẩn format IDP / BC)",
                "duration": "165 phút",
                "questions_count": 82,
                "badge": "Full Academic Test",
            },
        ],
        "chapters": [],
    },
    # ==================== CẤU TRÚC CHUYÊN BIỆT TIẾNG ANH: VSTEP ====================
    "tieng-anh_vstep": {
        "is_language": True,
        "subject_name": "Luyện thi VSTEP (Khung NLNN 6 Bậc B1 · B2 · C1)",
        "grade_name": "Tiếng Anh",
        "grade_id": "tieng-anh",
        "icon": "📜",
        "color": "#0d9488",
        "description": "Chương trình luyện thi VSTEP chuẩn Bộ GD&ĐT: Bài test năng lực đầu vào 4 kỹ năng, phần Study bài giảng B1-B2-C1 và phần Practice luyện đề thi thật có bấm giờ.",
        "placement_test": {
            "title": "Bài Test Đánh Giá Năng Lực VSTEP Đầu Vào (Khung 6 Bậc B1 - B2 - C1)",
            "desc": "Bài test chẩn đoán 45 phút bao quát 4 kỹ năng Nghe, Đọc, Viết, Nói giúp xác định cấp độ năng lực hiện tại theo Khung NLNN Việt Nam và gợi ý lộ trình đạt chuẩn B1, B2 hoặc C1.",
            "questions_count": 50,
            "duration_minutes": 45,
            "skills": ["Listening", "Reading", "Writing", "Speaking"],
            "badge": "Test Năng Lực VSTEP",
        },
        "study_skills": [
            {
                "id": "listening",
                "name": "Listening (Kỹ năng Nghe VSTEP)",
                "icon": "🎧",
                "desc": "Chiến thuật nghe 3 phần: Part 1 Thông báo & Hướng dẫn ngắn, Part 2 Hội thoại 2-3 người, Part 3 Thuyết trình học thuật",
                "lessons": [
                    {
                        "title": "Unit 1: Part 1 - 8 Hướng dẫn & Thông báo đời sống hàng ngày",
                        "badge": "Part 1 Announcements",
                        "duration": "35 phút",
                        "topics": "Nghe số, thời gian, địa điểm",
                    },
                    {
                        "title": "Unit 2: Part 2 - 3 Cuộc hội thoại và bẫy thông tin gây nhiễu",
                        "badge": "Part 2 Conversations",
                        "duration": "45 phút",
                        "topics": "Phân tích câu hỏi ngụ ý",
                    },
                    {
                        "title": "Unit 3: Part 3 - 3 Bài giảng học thuật & Bắt từ khóa chuyên ngành",
                        "badge": "Part 3 Lectures",
                        "duration": "50 phút",
                        "topics": "Ghi chú nhanh khi nghe",
                    },
                    {
                        "title": "Unit 4: Tổng ôn mẹo tránh bẫy phát âm nối âm & Giảm âm trong đề thi VSTEP",
                        "badge": "Mẹo Nghe VSTEP",
                        "duration": "40 phút",
                        "topics": "Ngữ điệu và trọng âm",
                    },
                ],
            },
            {
                "id": "reading",
                "name": "Reading (Kỹ năng Đọc VSTEP)",
                "icon": "📖",
                "desc": "Kỹ thuật giải quyết 4 bài đọc hiểu 40 câu hỏi từ dễ đến khó (B1 đến C1)",
                "lessons": [
                    {
                        "title": "Unit 1: Passage 1 - Đọc hiểu thư từ, email và thông báo công sở (Cấp độ B1)",
                        "badge": "Passage 1 (B1)",
                        "duration": "40 phút",
                        "topics": "Câu hỏi tìm thông tin trực tiếp",
                    },
                    {
                        "title": "Unit 2: Passage 2 - Bài báo khoa học phổ thông & Giải thích từ theo ngữ cảnh",
                        "badge": "Passage 2 (B2)",
                        "duration": "45 phút",
                        "topics": "Đoán nghĩa từ vựng mới",
                    },
                    {
                        "title": "Unit 3: Passage 3 - Văn bản phân tích lịch sử, văn hóa & Xác định ý chính",
                        "badge": "Passage 3 (B2-C1)",
                        "duration": "50 phút",
                        "topics": "Kỹ thuật Skimming & Scanning",
                    },
                    {
                        "title": "Unit 4: Passage 4 - Bài nghiên cứu học thuật chuyên sâu & Câu hỏi suy luận ngụ ý",
                        "badge": "Passage 4 (C1)",
                        "duration": "55 phút",
                        "topics": "Thái độ tác giả & Đại từ thay thế",
                    },
                ],
            },
            {
                "id": "writing",
                "name": "Writing (Kỹ năng Viết VSTEP)",
                "icon": "✍️",
                "desc": "Phương pháp viết Task 1 Thư/Email tương tác và Task 2 Bài luận tranh luận 250 từ",
                "lessons": [
                    {
                        "title": "Unit 1: Task 1 - Viết thư thân mật (Informal Letter) cảm ơn, xin lỗi, mời bạn",
                        "badge": "Task 1 Informal",
                        "duration": "40 phút",
                        "topics": "Cấu trúc thư 120 từ chuẩn B1",
                    },
                    {
                        "title": "Unit 2: Task 1 - Viết thư trang trọng (Formal Letter/Email) phàn nàn, xin việc, hỏi thông tin",
                        "badge": "Task 1 Formal",
                        "duration": "45 phút",
                        "topics": "Văn phong học thuật chuẩn B2",
                    },
                    {
                        "title": "Unit 3: Task 2 - Dạng bài Opinion Essay (Đồng ý hay không đồng ý)",
                        "badge": "Task 2 Opinion",
                        "duration": "50 phút",
                        "topics": "Dàn ý mở bài - thân bài - kết bài",
                    },
                    {
                        "title": "Unit 4: Task 2 - Dạng bài Problem & Solution, Discussion Essay & Bộ từ vựng liên kết C1",
                        "badge": "Task 2 Essay C1",
                        "duration": "55 phút",
                        "topics": "Từ nối Cohesive Devices",
                    },
                ],
            },
            {
                "id": "speaking",
                "name": "Speaking (Kỹ năng Nói VSTEP)",
                "icon": "🗣️",
                "desc": "Luyện 3 phần thi nói trên máy tính: Tương tác xã hội, Thảo luận giải pháp và Phát triển chủ đề",
                "lessons": [
                    {
                        "title": "Unit 1: Part 1 - Social Interaction: Phản xạ trả lời 3-6 câu hỏi đời sống quen thuộc",
                        "badge": "Part 1 Interaction",
                        "duration": "35 phút",
                        "topics": "Sở thích, gia đình, công việc",
                    },
                    {
                        "title": "Unit 2: Part 2 - Solution Discussion: So sánh 3 lựa chọn và bảo vệ quan điểm",
                        "badge": "Part 2 Discussion",
                        "duration": "45 phút",
                        "topics": "Cấu trúc so sánh & Lập luận",
                    },
                    {
                        "title": "Unit 3: Part 3 - Topic Development: Phát triển sơ đồ tư duy (Mindmap) và phản biện",
                        "badge": "Part 3 Topic Dev",
                        "duration": "50 phút",
                        "topics": "Mở rộng ý tưởng logic",
                    },
                    {
                        "title": "Unit 4: Chiến thuật quản lý thời gian thi nói trên máy tính và phát âm chuẩn tự nhiên",
                        "badge": "Phản xạ trên máy",
                        "duration": "40 phút",
                        "topics": "Ngữ điệu & Giữ nhịp nói",
                    },
                ],
            },
        ],
        "practice_skills": [
            {
                "id": "listening_practice",
                "name": "Listening Practice (Luyện đề Nghe)",
                "icon": "🎧",
                "desc": "Ngân hàng đề thi Nghe VSTEP 3 Parts có audio chất lượng cao và lời giải chi tiết",
                "exercises": [
                    {
                        "title": "Luyện đề Part 1: Bộ 8 câu hỏi thông báo trạm xe, sân bay, cửa hàng",
                        "questions_count": 8,
                        "duration": "10 phút",
                        "badge": "Part 1 Drill",
                    },
                    {
                        "title": "Luyện đề Part 2: 3 Bài hội thoại công sở và phỏng vấn du lịch",
                        "questions_count": 12,
                        "duration": "15 phút",
                        "badge": "Part 2 Drill",
                    },
                    {
                        "title": "Luyện đề Part 3: 3 Bài thuyết trình học thuật chuyên đề kinh tế, môi trường",
                        "questions_count": 15,
                        "duration": "20 phút",
                        "badge": "Part 3 Drill",
                    },
                    {
                        "title": "Đề thi thử VSTEP Listening Full 35 câu (Thời gian làm bài 40 phút)",
                        "questions_count": 35,
                        "duration": "40 phút",
                        "badge": "Full Listening",
                        "highlight": True,
                    },
                ],
            },
            {
                "id": "reading_practice",
                "name": "Reading Practice (Luyện đề Đọc)",
                "icon": "📖",
                "desc": "Bộ đề thi Đọc VSTEP 40 câu hỏi phân cấp độ rõ ràng từ B1 đến C1",
                "exercises": [
                    {
                        "title": "Luyện đề Passage 1: Đọc hiểu 10 câu chủ đề đời sống & công việc",
                        "questions_count": 10,
                        "duration": "15 phút",
                        "badge": "Passage 1",
                    },
                    {
                        "title": "Luyện đề Passage 2: Đọc hiểu 10 câu chủ đề tiến bộ công nghệ",
                        "questions_count": 10,
                        "duration": "15 phút",
                        "badge": "Passage 2",
                    },
                    {
                        "title": "Luyện đề Passage 3: Đọc hiểu 10 câu phân tích giáo dục & tâm lý",
                        "questions_count": 10,
                        "duration": "15 phút",
                        "badge": "Passage 3",
                    },
                    {
                        "title": "Đề thi thử VSTEP Reading Full 40 câu (Thời gian làm bài 60 phút)",
                        "questions_count": 40,
                        "duration": "60 phút",
                        "badge": "Full Reading",
                        "highlight": True,
                    },
                ],
            },
            {
                "id": "writing_practice",
                "name": "Writing Practice (Luyện đề Viết)",
                "icon": "✍️",
                "desc": "Bộ đề thi Viết VSTEP Task 1 & Task 2 kèm bài viết mẫu điểm cao",
                "exercises": [
                    {
                        "title": "Luyện đề Task 1: Viết email cho đồng nghiệp thảo luận dự án (120 từ)",
                        "questions_count": 1,
                        "duration": "20 phút",
                        "badge": "Email Practice",
                    },
                    {
                        "title": "Luyện đề Task 1: Viết thư phàn nàn dịch vụ khách hàng (120 từ)",
                        "questions_count": 1,
                        "duration": "20 phút",
                        "badge": "Complaint Letter",
                    },
                    {
                        "title": "Luyện đề Task 2: Bài luận tác động của mạng xã hội đến giới trẻ (250 từ)",
                        "questions_count": 1,
                        "duration": "40 phút",
                        "badge": "Essay Task 2",
                    },
                    {
                        "title": "Đề thi thử VSTEP Writing Full (Task 1 + Task 2 - 60 phút)",
                        "questions_count": 2,
                        "duration": "60 phút",
                        "badge": "Full Writing",
                        "highlight": True,
                    },
                ],
            },
            {
                "id": "speaking_practice",
                "name": "Speaking Practice (Luyện đề Nói)",
                "icon": "🗣️",
                "desc": "Phần mềm mô phỏng thi Nói VSTEP trên máy tính có đồng hồ đếm ngược và câu trả lời mẫu",
                "exercises": [
                    {
                        "title": "Luyện đề Part 1: Bộ 6 câu hỏi chủ đề Thói quen đọc sách & Du lịch",
                        "questions_count": 6,
                        "duration": "3 phút",
                        "badge": "Part 1 Practice",
                    },
                    {
                        "title": "Luyện đề Part 2: Thảo luận chọn địa điểm tổ chức sự kiện cho công ty",
                        "questions_count": 1,
                        "duration": "4 phút",
                        "badge": "Part 2 Practice",
                    },
                    {
                        "title": "Luyện đề Part 3: Thuyết trình về lợi ích của làm việc từ xa (Remote Work)",
                        "questions_count": 1,
                        "duration": "5 phút",
                        "badge": "Part 3 Practice",
                    },
                    {
                        "title": "Thi thử VSTEP Speaking Full Test 3 phần (Thời gian thi 12 phút)",
                        "questions_count": 3,
                        "duration": "12 phút",
                        "badge": "Full Speaking Test",
                        "highlight": True,
                    },
                ],
            },
        ],
        "full_tests": [
            {
                "title": "Đề thi thử VSTEP B1-B2 Full Test 01 (Chuẩn cấu trúc 4 kỹ năng Bộ GD&ĐT)",
                "duration": "172 phút",
                "questions_count": 80,
                "badge": "VSTEP B1-B2",
            },
            {
                "title": "Đề thi thử VSTEP B2-C1 Full Test 02 (Đề thi mẫu chuẩn hóa các trường ĐH Trọng điểm)",
                "duration": "172 phút",
                "questions_count": 80,
                "badge": "VSTEP B2-C1",
            },
        ],
        "chapters": [],
    },
    # ==================== CẤU TRÚC CHUYÊN BIỆT TIẾNG TRUNG: HSK ====================
    "tieng-trung_hsk": {
        "is_language": True,
        "subject_slug": "hsk",
        "subject_name": "Luyện thi HSK (Cấp độ 1 - 6)",
        "grade_name": "Tiếng Trung",
        "grade_id": "tieng-trung",
        "icon": "🇨🇳",
        "color": "#dc2626",
        "description": "Chương trình ôn luyện HSK từ cấp 1 đến cấp 6: Bài test năng lực HSK đầu vào, phần Study bài giảng 4 kỹ năng và phần Practice luyện đề thi có bấm giờ.",
        "placement_test": {
            "title": "Bài Test Đánh Giá Năng Lực Tiếng Trung HSK Đầu Vào",
            "desc": "Bài test chuẩn đoán 40 phút bao quát các kỹ năng Nghe, Đọc, Viết để xác định chính xác cấp độ HSK hiện tại (HSK 1 đến HSK 6) và gợi ý lộ trình phù hợp.",
            "questions_count": 40,
            "duration_minutes": 40,
            "skills": ["Nghe hiểu (听力)", "Đọc hiểu (阅读)", "Viết (书写)", "Khẩu ngữ (口语)"],
            "badge": "HSK Diagnostic Test",
        },
        "study_skills": [
            {
                "id": "listening",
                "name": "Nghe hiểu 听力 (Listening)",
                "icon": "🎧",
                "desc": "Rèn luyện kỹ năng nghe nhận biết ngữ âm Pinyin, hội thoại ngắn và đoạn văn dài",
                "lessons": [
                    {
                        "title": "Unit 1: Nghe phân biệt thanh mẫu, vận mẫu và biến điệu thanh 3 (变调)",
                        "badge": "Pinyin Hearing",
                        "duration": "30 phút",
                    },
                    {
                        "title": "Unit 2: Kỹ thuật nghe bắt từ khóa trong câu hỏi đúng sai (判断对错)",
                        "badge": "True / False",
                        "duration": "35 phút",
                    },
                    {
                        "title": "Unit 3: Chiến thuật nghe đoạn hội thoại 2 lượt và bắt chi tiết số liệu, địa điểm",
                        "badge": "Short Dialogue",
                        "duration": "40 phút",
                    },
                    {
                        "title": "Unit 4: Nghe đoạn văn dài độc thoại và xử lý chuỗi câu hỏi liên tiếp",
                        "badge": "Monologue",
                        "duration": "45 phút",
                    },
                ],
            },
            {
                "id": "reading",
                "name": "Đọc hiểu 阅读 (Reading)",
                "icon": "📖",
                "desc": "Hệ thống từ vựng HSK 3.0, ngữ pháp câu chữ 把, 被, câu so sánh và đọc hiểu đoạn văn",
                "lessons": [
                    {
                        "title": "Unit 1: Quy tắc thứ tự các nét chữ Hán & Nhận diện 100 bộ thủ cốt lõi",
                        "badge": "Chữ Hán & Bộ thủ",
                        "duration": "40 phút",
                    },
                    {
                        "title": "Unit 2: 1200 Từ vựng trọng điểm phân chia theo chủ đề HSK 3.0",
                        "badge": "Từ vựng HSK",
                        "duration": "45 phút",
                    },
                    {
                        "title": "Unit 3: Cấu trúc ngữ pháp quan trọng: Câu chữ 把, câu bị động 被, câu so sánh 比",
                        "badge": "Ngữ pháp cốt lõi",
                        "duration": "50 phút",
                    },
                    {
                        "title": "Unit 4: Kỹ thuật đọc lướt tìm ý chính và phương pháp chọn từ điền vào chỗ trống",
                        "badge": "Đọc hiểu nhanh",
                        "duration": "45 phút",
                    },
                ],
            },
            {
                "id": "writing",
                "name": "Viết & Đặt câu 书写 (Writing)",
                "icon": "✍️",
                "desc": "Quy tắc sắp xếp trật tự từ trong câu tiếng Trung, viết chữ Hán và đặt câu theo tranh",
                "lessons": [
                    {
                        "title": "Unit 1: Trật tự từ trong câu tiếng Trung: Chủ - Trạng - Động - Bổ - Tân",
                        "badge": "Trật tự từ",
                        "duration": "40 phút",
                    },
                    {
                        "title": "Unit 2: Kỹ năng sắp xếp các cụm từ xáo trộn thành câu hoàn chỉnh (连词成句)",
                        "badge": "Xếp câu HSK",
                        "duration": "45 phút",
                    },
                    {
                        "title": "Unit 3: Phương pháp đặt câu mô tả bức tranh có sử dụng từ khóa cho trước",
                        "badge": "Viết theo tranh",
                        "duration": "45 phút",
                    },
                    {
                        "title": "Unit 4: Viết bài văn ngắn 80-100 chữ cho đề thi HSK 5 (Kể chuyện & Nghị luận)",
                        "badge": "Viết đoạn văn",
                        "duration": "50 phút",
                    },
                ],
            },
            {
                "id": "speaking",
                "name": "Khẩu ngữ & Giao tiếp 口语 (Speaking)",
                "icon": "🗣️",
                "desc": "Luyện phát âm chuẩn giọng Bắc Kinh, các mẫu câu giao tiếp và thi nói HSKK",
                "lessons": [
                    {
                        "title": "Unit 1: Luyện phát âm chuẩn âm cuốn lưỡi er, âm bật hơi và thanh nhẹ",
                        "badge": "Chuẩn âm Pinyin",
                        "duration": "30 phút",
                    },
                    {
                        "title": "Unit 2: Các mẫu câu giao tiếp hàng ngày: Mua sắm, hỏi đường, đặt phòng",
                        "badge": "Giao tiếp đời sống",
                        "duration": "35 phút",
                    },
                    {
                        "title": "Unit 3: Kỹ năng nghe và lặp lại câu trong bài thi HSKK (听后重复)",
                        "badge": "HSKK Sơ cấp",
                        "duration": "40 phút",
                    },
                    {
                        "title": "Unit 4: Thuyết trình theo chủ đề và trả lời câu hỏi phỏng vấn HSKK Trung/Cao cấp",
                        "badge": "HSKK Trung cấp",
                        "duration": "45 phút",
                    },
                ],
            },
        ],
        "practice_skills": [
            {
                "id": "listening_practice",
                "name": "Luyện đề Nghe hiểu (听力练习)",
                "icon": "🎧",
                "desc": "Bộ đề luyện nghe có audio tiếng phổ thông chuẩn và chấm điểm tức thì",
                "exercises": [
                    {
                        "title": "Luyện nghe HSK: 40 Câu hỏi đúng sai chọn tranh (Có Audio)",
                        "questions_count": 40,
                        "duration": "25 phút",
                        "badge": "Nghe tranh HSK",
                    },
                    {
                        "title": "Luyện nghe HSK: 30 Đoạn hội thoại 2 câu có giải thích từ khóa",
                        "questions_count": 30,
                        "duration": "25 phút",
                        "badge": "Nghe hội thoại",
                    },
                    {
                        "title": "Đề thi thử HSK Nghe hiểu trọn vẹn (Chuẩn cấu trúc Hanban)",
                        "questions_count": 45,
                        "duration": "35 phút",
                        "badge": "Full Nghe HSK",
                        "highlight": True,
                    },
                ],
            },
            {
                "id": "reading_practice",
                "name": "Luyện đề Đọc hiểu (阅读练习)",
                "icon": "📖",
                "desc": "Ngân hàng bài tập đọc hiểu trắc nghiệm chọn từ điền chỗ trống và đọc đoạn văn",
                "exercises": [
                    {
                        "title": "Luyện đọc: 50 Câu chọn từ thích hợp điền vào chỗ trống",
                        "questions_count": 50,
                        "duration": "30 phút",
                        "badge": "Điền từ HSK",
                    },
                    {
                        "title": "Luyện đọc: 30 Bài đọc hiểu đoạn văn ngắn chọn đáp án đúng",
                        "questions_count": 30,
                        "duration": "35 phút",
                        "badge": "Đọc hiểu HSK",
                    },
                    {
                        "title": "Đề thi thử HSK Đọc hiểu Full (40 câu / 40 phút)",
                        "questions_count": 40,
                        "duration": "40 phút",
                        "badge": "Full Đọc HSK",
                        "highlight": True,
                    },
                ],
            },
            {
                "id": "writing_practice",
                "name": "Luyện đề Viết (书写练习)",
                "icon": "✍️",
                "desc": "Luyện xếp câu và đặt câu theo tranh có đáp án gợi ý và mẫu câu chuẩn",
                "exercises": [
                    {
                        "title": "Luyện tập: 30 Câu xếp cụm từ thành câu hoàn chỉnh (連詞成句)",
                        "questions_count": 30,
                        "duration": "30 phút",
                        "badge": "Xếp câu",
                    },
                    {
                        "title": "Luyện tập: 15 Bài đặt câu theo tranh có cho trước từ gợi ý",
                        "questions_count": 15,
                        "duration": "30 phút",
                        "badge": "Đặt câu theo tranh",
                    },
                ],
            },
            {
                "id": "speaking_practice",
                "name": "Luyện khẩu ngữ HSKK (口语练习)",
                "icon": "🗣️",
                "desc": "Phòng luyện nói tương tác có audio mẫu và hướng dẫn phát âm",
                "exercises": [
                    {
                        "title": "Luyện nghe và nhắc lại 20 câu khẩu ngữ thông dụng (听后重复)",
                        "questions_count": 20,
                        "duration": "20 phút",
                        "badge": "Luyện nhắc lại",
                    },
                    {
                        "title": "Luyện trả lời 10 câu hỏi giao tiếp đời sống và công việc",
                        "questions_count": 10,
                        "duration": "25 phút",
                        "badge": "Hỏi đáp HSKK",
                    },
                ],
            },
        ],
        "full_tests": [
            {
                "title": "Đề thi thử HSK 3 Full Test (Nghe - Đọc - Viết có Audio)",
                "duration": "90 phút",
                "questions_count": 80,
                "badge": "HSK 3 Mock Test",
            },
            {
                "title": "Đề thi thử HSK 4 Full Test (Chuẩn cấu trúc Hanban mới nhất)",
                "duration": "105 phút",
                "questions_count": 100,
                "badge": "HSK 4 Mock Test",
            },
        ],
        "chapters": [],
    },
    # ==================== CẤU TRÚC CHUYÊN BIỆT TIẾNG NHẬT: JLPT ====================
    "tieng-nhat_jlpt": {
        "is_language": True,
        "subject_slug": "jlpt",
        "subject_name": "Luyện thi JLPT (Cấp độ N5 - N1)",
        "grade_name": "Tiếng Nhật",
        "grade_id": "tieng-nhat",
        "icon": "🇯🇵",
        "color": "#ea580c",
        "description": "Chương trình ôn luyện chứng chỉ JLPT từ N5 đến N1: Bài test năng lực JLPT đầu vào, phần Study bài giảng 4 kỹ năng và phần Practice luyện đề thi có bấm giờ.",
        "placement_test": {
            "title": "Bài Test Đánh Giá Năng Lực Tiếng Nhật JLPT Đầu Vào",
            "desc": "Bài test chuẩn đoán 45 phút bao quát các kỹ năng Từ vựng/Kanji, Ngữ pháp, Đọc hiểu và Nghe hiểu để xác định trình độ JLPT hiện tại (N5, N4, N3, N2, N1) và đề xuất lộ trình tối ưu.",
            "questions_count": 45,
            "duration_minutes": 45,
            "skills": ["Kanji/Từ vựng (文字・語彙)", "Ngữ pháp (文法)", "Đọc hiểu (読解)", "Nghe hiểu (聴解)"],
            "badge": "JLPT Diagnostic Test",
        },
        "study_skills": [
            {
                "id": "listening",
                "name": "Nghe hiểu 聴解 (Listening - Choukai)",
                "icon": "🎧",
                "desc": "Luyện phản xạ nghe Choukai, bắt từ nối, ngữ điệu và các mẫu câu hội thoại thường ngày",
                "lessons": [
                    {
                        "title": "Unit 1: Luyện nghe hiểu chủ đề (課題理解) - Xác định hành động tiếp theo của nhân vật",
                        "badge": "Choukai N3-N2",
                        "duration": "35 phút",
                    },
                    {
                        "title": "Unit 2: Luyện nghe nắm bắt điểm cốt lõi (ポイント理解) - Lọc thông tin cần thiết",
                        "badge": "Key Points",
                        "duration": "40 phút",
                    },
                    {
                        "title": "Unit 3: Kỹ thuật làm bài nghe hiểu khái quát (概要理解) - Bắt ý kiến chung của người nói",
                        "badge": "General Idea",
                        "duration": "40 phút",
                    },
                    {
                        "title": "Unit 4: Luyện phản xạ nhanh câu hỏi ứng đáp tức thì (即時応答)",
                        "badge": "Quick Response",
                        "duration": "35 phút",
                    },
                ],
            },
            {
                "id": "reading",
                "name": "Đọc hiểu 読解 (Reading - Dokkai)",
                "icon": "📖",
                "desc": "Kỹ thuật đọc đoản văn, trung văn, trường văn và bài tìm kiếm thông tin tra cứu",
                "lessons": [
                    {
                        "title": "Unit 1: Đọc hiểu đoản văn (短文) - Kỹ thuật bắt từ khóa và câu chủ đề đoạn văn",
                        "badge": "Short Reading",
                        "duration": "40 phút",
                    },
                    {
                        "title": "Unit 2: Đọc hiểu trung văn (中文) - Phân tích mối quan hệ nguyên nhân kết quả",
                        "badge": "Medium Reading",
                        "duration": "45 phút",
                    },
                    {
                        "title": "Unit 3: Đọc hiểu trường văn nghị luận (長文) - Nắm bắt quan điểm của tác giả",
                        "badge": "Long Reading",
                        "duration": "50 phút",
                    },
                    {
                        "title": "Unit 4: Kỹ thuật tra cứu thông tin (情報検索) - Đọc lướt tờ rơi, thông báo, bảng giá",
                        "badge": "Info Search",
                        "duration": "35 phút",
                    },
                ],
            },
            {
                "id": "writing",
                "name": "Chữ Hán & Từ vựng 文字・語彙 (Vocabulary & Kanji)",
                "icon": "🈴",
                "desc": "Chinh phục 2136 chữ Hán Joyo Kanji, âm On, âm Kun, từ đồng nghĩa và từ ghép",
                "lessons": [
                    {
                        "title": "Unit 1: 300 Kanji N5-N4 căn bản & Mẹo ghi nhớ chữ Hán qua hình ảnh bộ thủ",
                        "badge": "Kanji Sơ cấp",
                        "duration": "45 phút",
                    },
                    {
                        "title": "Unit 2: 650 Kanji N3 trọng điểm & Các cặp từ ghép dễ gây nhầm lẫn",
                        "badge": "Kanji Trung cấp",
                        "duration": "50 phút",
                    },
                    {
                        "title": "Unit 3: Từ đồng nghĩa, từ trái nghĩa và từ vựng biến đổi theo ngữ cảnh",
                        "badge": "Từ vựng JLPT",
                        "duration": "40 phút",
                    },
                    {
                        "title": "Unit 4: Hệ thống từ tượng thanh, từ tượng hình (Gitaigo / Giongo) trong văn nói",
                        "badge": "Từ tượng hình",
                        "duration": "35 phút",
                    },
                ],
            },
            {
                "id": "speaking",
                "name": "Ngữ pháp & Hội thoại 文法・会話 (Grammar & Kaiwa)",
                "icon": "🗣️",
                "desc": "150 mẫu ngữ pháp Soumatome, kính ngữ Sonkeigo, Kenjougo và hội thoại Kaiwa",
                "lessons": [
                    {
                        "title": "Unit 1: 50 Mẫu ngữ pháp chỉ lý do, điều kiện và nhượng bộ: わけ, ため, から",
                        "badge": "Ngữ pháp trọng điểm",
                        "duration": "45 phút",
                    },
                    {
                        "title": "Unit 2: Kính ngữ chuẩn công sở: Tôn kính ngữ (尊敬語) & Khiêm nhường ngữ (謙譲語)",
                        "badge": "Kính ngữ Keigo",
                        "duration": "45 phút",
                    },
                    {
                        "title": "Unit 3: Kỹ thuật làm bài sắp xếp dấu sao (★の文法) hoàn chỉnh câu",
                        "badge": "Ngữ pháp dấu sao",
                        "duration": "40 phút",
                    },
                    {
                        "title": "Unit 4: Các mẫu câu giao tiếp Kaiwa tự nhiên của người bản xứ Nhật Bản",
                        "badge": "Kaiwa Giao tiếp",
                        "duration": "40 phút",
                    },
                ],
            },
        ],
        "practice_skills": [
            {
                "id": "listening_practice",
                "name": "Luyện đề Nghe hiểu Choukai (聴解練習)",
                "icon": "🎧",
                "desc": "Kho đề thi Choukai có audio giọng đọc chuẩn kỳ thi JLPT các năm",
                "exercises": [
                    {
                        "title": "Luyện nghe: 20 Bài nghe hiểu chủ đề có tranh minh họa",
                        "questions_count": 20,
                        "duration": "25 phút",
                        "badge": "Choukai có tranh",
                    },
                    {
                        "title": "Luyện nghe: 20 Bài câu hỏi phản xạ ứng đáp tức thì",
                        "questions_count": 20,
                        "duration": "20 phút",
                        "badge": "Phản xạ tức thì",
                    },
                    {
                        "title": "Đề thi thử JLPT Choukai trọn bộ (Có audio & Tapescript)",
                        "questions_count": 32,
                        "duration": "40 phút",
                        "badge": "Full Choukai",
                        "highlight": True,
                    },
                ],
            },
            {
                "id": "reading_practice",
                "name": "Luyện đề Đọc hiểu Dokkai (読解練習)",
                "icon": "📖",
                "desc": "Bộ bài tập luyện đọc đoản văn, trung văn và bài tra cứu thông tin",
                "exercises": [
                    {
                        "title": "Luyện đọc: 15 Bài đoản văn rèn kỹ năng bắt từ nối (Nhưng, Vì vậy...)",
                        "questions_count": 15,
                        "duration": "30 phút",
                        "badge": "Dokkai Đoản văn",
                    },
                    {
                        "title": "Luyện đọc: 10 Bài trung văn và trường văn nghị luận xã hội",
                        "questions_count": 20,
                        "duration": "45 phút",
                        "badge": "Dokkai Trung văn",
                    },
                    {
                        "title": "Đề thi thử JLPT Đọc hiểu Full (16 câu đọc hiểu / 60 phút)",
                        "questions_count": 16,
                        "duration": "60 phút",
                        "badge": "Full Dokkai",
                        "highlight": True,
                    },
                ],
            },
            {
                "id": "writing_practice",
                "name": "Luyện đề Kanji & Từ vựng (語彙練習)",
                "icon": "🈴",
                "desc": "Kho câu hỏi trắc nghiệm cách đọc Kanji, chọn chữ Hán và cách dùng từ",
                "exercises": [
                    {
                        "title": "Luyện đề: 50 Câu trắc nghiệm cách đọc chữ Hán (漢字読み)",
                        "questions_count": 50,
                        "duration": "25 phút",
                        "badge": "Cách đọc Kanji",
                    },
                    {
                        "title": "Luyện đề: 40 Câu chọn chữ Hán thích hợp điền vào câu",
                        "questions_count": 40,
                        "duration": "25 phút",
                        "badge": "Chọn Kanji",
                    },
                    {
                        "title": "Luyện đề: 30 Câu cách dùng từ đúng trong ngữ cảnh (文脈規定)",
                        "questions_count": 30,
                        "duration": "25 phút",
                        "badge": "Cách dùng từ",
                    },
                ],
            },
            {
                "id": "speaking_practice",
                "name": "Luyện đề Ngữ pháp & Dấu sao (文法練習)",
                "icon": "🗣️",
                "desc": "Chuyên đề bài tập chọn dạng ngữ pháp đúng và sắp xếp câu dấu sao",
                "exercises": [
                    {
                        "title": "Luyện đề: 50 Câu chọn mẫu ngữ pháp thích hợp điền vào câu",
                        "questions_count": 50,
                        "duration": "30 phút",
                        "badge": "Ngữ pháp chọn",
                    },
                    {
                        "title": "Luyện đề: 25 Bài sắp xếp trật tự từ có dấu sao (★の文法)",
                        "questions_count": 25,
                        "duration": "30 phút",
                        "badge": "Dấu sao ★",
                    },
                ],
            },
        ],
        "full_tests": [
            {
                "title": "Đề thi thử JLPT N3 Full Test (Từ vựng/Kanji - Ngữ pháp/Đọc hiểu - Nghe hiểu)",
                "duration": "140 phút",
                "questions_count": 95,
                "badge": "JLPT N3 Mock",
            },
            {
                "title": "Đề thi thử JLPT N2 Full Test (Bám sát đề thi thật các năm)",
                "duration": "155 phút",
                "questions_count": 105,
                "badge": "JLPT N2 Mock",
            },
        ],
        "chapters": [],
    },
}


def generate_default_curriculum(grade_id: str, grade_name: str, subject_name: str, subject_slug: str) -> dict[str, Any]:
    """Generates a structured, professional curriculum fallback for any grade/subject
    with 4 realistic chapters and 3-5 lessons each, ensuring no subject is ever blank.
    """
    clean_subj = re.sub(r"(Lớp\s*\d+|TNTHPT|\b\d+\b)", "", subject_name).strip()
    icon = "📘"
    if "toán" in clean_subj.lower():
        icon = "📐"
    elif "lí" in clean_subj.lower() or "vật" in clean_subj.lower():
        icon = "⚡"
    elif "hóa" in clean_subj.lower():
        icon = "🧪"
    elif "sinh" in clean_subj.lower():
        icon = "🧬"
    elif "anh" in clean_subj.lower() or "ngoại ngữ" in clean_subj.lower():
        icon = "🌐"
    elif "văn" in clean_subj.lower() or "tiếng việt" in clean_subj.lower():
        icon = "📖"
    elif "sử" in clean_subj.lower() or "địa" in clean_subj.lower():
        icon = "🏛️"
    elif "tin" in clean_subj.lower():
        icon = "💻"

    chapters = [
        {
            "id": "c1",
            "title": f"Chương 1: Kiến thức trọng tâm & Khái niệm nền tảng môn {clean_subj}",
            "desc": f"Tổng quan lý thuyết cơ bản, các định lý và định nghĩa cốt lõi của chương trình {grade_name}.",
            "lessons": [
                {
                    "id": "b1",
                    "title": f"Bài 1: Khái niệm & Định lý mở đầu môn {clean_subj}",
                    "badge": "Lý thuyết & Trắc nghiệm",
                    "questions_count": 25,
                },
                {
                    "id": "b2",
                    "title": "Bài 2: Phương pháp giải các dạng bài tập căn bản",
                    "badge": "Ví dụ mẫu",
                    "questions_count": 30,
                },
                {
                    "id": "b3",
                    "title": "Bài 3: Kỹ năng ứng dụng công thức và làm bài nhanh",
                    "badge": "Bài tập tự luyện",
                    "questions_count": 25,
                },
                {
                    "id": "b-ot1",
                    "title": f"Ôn tập & Đề kiểm tra Chương 1 môn {clean_subj}",
                    "badge": "Đề tự luyện",
                    "is_exam": True,
                    "questions_count": 40,
                },
            ],
        },
        {
            "id": "c2",
            "title": "Chương 2: Kỹ năng phân tích & Các dạng bài tập nâng cao",
            "desc": "Các dạng bài toán thường xuất hiện trong đề kiểm tra giữa kỳ và bài thi định kỳ.",
            "lessons": [
                {
                    "id": "b4",
                    "title": "Bài 4: Phân loại dạng toán & Công thức biến đổi",
                    "badge": "Dạng bài trọng tâm",
                    "questions_count": 30,
                },
                {
                    "id": "b5",
                    "title": "Bài 5: Bài tập rèn luyện tư duy & Phương pháp loại trừ",
                    "badge": "Kỹ năng làm bài",
                    "questions_count": 35,
                },
                {
                    "id": "b6",
                    "title": "Bài 6: Thực hành giải đề kiểm tra 15 phút",
                    "badge": "Bấm giờ",
                    "questions_count": 20,
                },
                {
                    "id": "b-ot2",
                    "title": f"Ôn tập & Đề kiểm tra Chương 2 môn {clean_subj}",
                    "badge": "Đề tự luyện",
                    "is_exam": True,
                    "questions_count": 40,
                },
            ],
        },
        {
            "id": "c3",
            "title": "Chương 3: Ứng dụng thực tế & Chuyên đề mở rộng",
            "desc": f"Gắn liền kiến thức môn {clean_subj} {grade_name} với các bài toán thực tiễn và mô hình ứng dụng.",
            "lessons": [
                {
                    "id": "b7",
                    "title": "Bài 7: Chuyên đề liên hệ thực tế & Bối cảnh mới",
                    "badge": "Thực tiễn",
                    "questions_count": 25,
                },
                {
                    "id": "b8",
                    "title": "Bài 8: Bài tập tổng hợp vận dụng cao",
                    "badge": "Điểm 9 - 10",
                    "questions_count": 30,
                },
                {
                    "id": "b-ot3",
                    "title": f"Đề kiểm tra học kỳ Chương 3 môn {clean_subj}",
                    "badge": "Đề tự luyện",
                    "is_exam": True,
                    "questions_count": 45,
                },
            ],
        },
        {
            "id": "c4",
            "title": "Chương 4: Tổng ôn học kỳ & Ngân hàng đề thi tự luyện",
            "desc": "Hệ thống hóa toàn bộ công thức, sơ đồ tư duy mindmap và đề thi thử bấm giờ chấm điểm tức thì.",
            "lessons": [
                {
                    "id": "b9",
                    "title": f"Bài 9: Sơ đồ tư duy toàn bộ chương trình {clean_subj} {grade_name}",
                    "badge": "Mindmap",
                    "questions_count": 20,
                },
                {
                    "id": "b10",
                    "title": "Bài 10: Bộ đề thi học kỳ 1 có bấm giờ & giải chi tiết",
                    "badge": "Thi thử",
                    "is_exam": True,
                    "questions_count": 50,
                },
                {
                    "id": "b11",
                    "title": "Bài 11: Bộ đề thi học kỳ 2 chuẩn cấu trúc Bộ GD&ĐT",
                    "badge": "Thi thử",
                    "is_exam": True,
                    "questions_count": 50,
                },
            ],
        },
    ]

    return {
        "subject_name": subject_name,
        "grade_name": grade_name,
        "grade_id": grade_id,
        "icon": icon,
        "color": "#1e40af",
        "description": f"Chương trình môn {subject_name} {grade_name} chuẩn SGK Kết nối tri thức, gồm các chương bài chi tiết, tóm tắt lý thuyết và ngân hàng câu hỏi tự luyện.",
        "chapters": chapters,
    }


def ensure_chapter_exams(curriculum: dict[str, Any]) -> dict[str, Any]:
    """Ensures every chapter has an 'Ôn tập & Đề thi tự luyện chương' item with is_exam=True."""
    for idx, ch in enumerate(curriculum.get("chapters", []), start=1):
        lessons = ch.setdefault("lessons", [])
        ch_title = ch.get("title", f"Chương {idx}")
        # Look for existing review or exam lesson
        exam_les = next(
            (
                l
                for l in lessons  # noqa: E741
                if l.get("is_exam")
                or "ôn tập" in l.get("title", "").lower()
                or "tự luyện" in l.get("title", "").lower()
                or "thi thử" in l.get("title", "").lower()
                or "kiểm tra" in l.get("title", "").lower()
            ),
            None,
        )
        if exam_les:
            exam_les["is_exam"] = True
            if "tự luyện" not in exam_les["title"].lower() and "thi thử" not in exam_les["title"].lower():
                exam_les["title"] = f"Ôn tập & Đề thi tự luyện {ch_title}"
            exam_les.setdefault("badge", "Đề tự luyện")
            exam_les.setdefault("questions_count", 40)
        else:
            lessons.append(
                {
                    "id": f"{ch.get('id', f'c{idx}')}-ot",
                    "title": f"Ôn tập & Đề thi tự luyện {ch_title}",
                    "badge": "Đề tự luyện",
                    "is_exam": True,
                    "questions_count": 40,
                    "db_cat_id": ch.get("db_cat_id"),
                }
            )
    return curriculum


def get_curriculum(grade_id: str, grade_name: str, subject_name: str, subject_slug: str) -> dict[str, Any]:
    """Returns curated curriculum data if present, otherwise returns generated standardized curriculum."""
    # Key normalization
    clean_grade = grade_id.lower().strip()
    clean_subj_slug = subject_slug.lower().strip().replace("_", "-")
    key = f"{clean_grade}_{clean_subj_slug}"

    result = None
    if key in CURRICULUM_DATA:
        result = CURRICULUM_DATA[key]
    elif clean_grade == "tieng-trung" and ("hsk" in clean_subj_slug):
        result = CURRICULUM_DATA.get("tieng-trung_hsk") or CURRICULUM_DATA.get("tieng-trung_hsk-3-4")
    elif clean_grade == "tieng-nhat" and (
        "jlpt" in clean_subj_slug or "kanji" in clean_subj_slug or "kaiwa" in clean_subj_slug
    ):
        result = CURRICULUM_DATA.get("tieng-nhat_jlpt") or CURRICULUM_DATA.get("tieng-nhat_jlpt-n3")
    elif clean_grade == "tieng-anh" and ("toeic" in clean_subj_slug):
        result = CURRICULUM_DATA.get("tieng-anh_toeic")
    elif clean_grade == "tieng-anh" and ("ielts" in clean_subj_slug):
        result = CURRICULUM_DATA.get("tieng-anh_ielts")
    elif clean_grade == "tieng-anh" and ("vstep" in clean_subj_slug):
        result = CURRICULUM_DATA.get("tieng-anh_vstep")
    else:
        # Matching by subject keywords (must match exact grade prefix, e.g. lop-1 != lop-11)
        for keyword in ["vat-li", "toan", "triet-hoc"]:
            if keyword in clean_subj_slug:
                for cur_key, cur_val in CURRICULUM_DATA.items():
                    grade_part = cur_key.split("_")[0]
                    if grade_part == clean_grade and keyword in cur_key:
                        result = cur_val
                        break
            if result:
                break

    if not result:
        result = generate_default_curriculum(grade_id, grade_name, subject_name, subject_slug)

    res = dict(ensure_chapter_exams(result))
    res["subject_slug"] = clean_subj_slug
    res["grade_id"] = clean_grade
    return res
