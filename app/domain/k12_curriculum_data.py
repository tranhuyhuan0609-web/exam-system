"""Comprehensive master curriculum data for Grades 1 to 12 according to SGK Kết nối tri thức."""


def get_master_k12_catalog():
    catalog = []

    # =========================================================================
    # LỚP 1
    # =========================================================================
    catalog.append(
        {
            "grade": 1,
            "subjects": [
                {
                    "slug": "toan-1",
                    "name": "Toán 1",
                    "subject_code": "MATH",
                    "icon": "📐",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Các số từ 0 đến 10",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Các số 1, 2, 3"},
                                {"id": "b2", "title": "Bài 2: Các số 4, 5, 6"},
                                {"id": "b3", "title": "Bài 3: Các số 7, 8, 9, 10 và số 0"},
                                {"id": "b4", "title": "Bài 4: So sánh số: Nhiều hơn, ít hơn, bằng nhau"},
                                {"id": "b5", "title": "Bài 5: Lớn hơn, bé hơn, dấu >, <, ="},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Làm quen với một số hình phẳng",
                            "lessons": [
                                {"id": "b6", "title": "Bài 6: Hình vuông, hình tròn, hình tam giác, hình chữ nhật"},
                                {"id": "b7", "title": "Bài 7: Vị trí và định hướng trong không gian"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Phép cộng và phép trừ trong phạm vi 10",
                            "lessons": [
                                {"id": "b8", "title": "Bài 8: Phép cộng trong phạm vi 10"},
                                {"id": "b9", "title": "Bài 9: Phép trừ trong phạm vi 10"},
                                {"id": "b10", "title": "Bài 10: Bảng cộng và bảng trừ trong phạm vi 10"},
                                {"id": "b11", "title": "Bài 11: Luyện tập chung phép tính trong phạm vi 10"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Làm quen với một số hình khối",
                            "lessons": [
                                {"id": "b12", "title": "Bài 12: Khối lập phương và khối hộp chữ nhật"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chương 5: Các số trong phạm vi 100",
                            "lessons": [
                                {"id": "b13", "title": "Bài 13: Các số tròn chục"},
                                {"id": "b14", "title": "Bài 14: Các số có hai chữ số"},
                                {"id": "b15", "title": "Bài 15: So sánh các số có hai chữ số"},
                            ],
                        },
                        {
                            "id": "c6",
                            "title": "Chương 6: Độ dài và đo độ dài",
                            "lessons": [
                                {"id": "b16", "title": "Bài 16: Dài hơn, ngắn hơn"},
                                {"id": "b17", "title": "Bài 17: Đơn vị xăng-ti-mét (cm)"},
                            ],
                        },
                        {
                            "id": "c7",
                            "title": "Chương 7: Phép cộng, trừ không nhớ trong phạm vi 100",
                            "lessons": [
                                {"id": "b18", "title": "Bài 18: Phép cộng số có hai chữ số với số có một chữ số"},
                                {"id": "b19", "title": "Bài 19: Phép trừ số có hai chữ số cho số có một chữ số"},
                                {"id": "b20", "title": "Bài 20: Phép cộng, trừ hai số có hai chữ số"},
                            ],
                        },
                        {
                            "id": "c8",
                            "title": "Chương 8: Thời gian, giờ và lịch",
                            "lessons": [
                                {"id": "b21", "title": "Bài 21: Đồng hồ và xem giờ đúng"},
                                {"id": "b22", "title": "Bài 22: Các ngày trong tuần"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tieng-viet-1",
                    "name": "Tiếng Việt 1",
                    "subject_code": "LIT",
                    "icon": "📖",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Làm quen âm, chữ cái và dấu thanh",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: A a, B b, C c"},
                                {"id": "b2", "title": "Bài 2: D d, Đ đ, E e, Ê ê"},
                                {"id": "b3", "title": "Bài 3: Các dấu thanh: sắc, huyền, hỏi, ngã, nặng"},
                                {"id": "b4", "title": "Bài 4: G g, H h, I i, K k"},
                                {"id": "b5", "title": "Bài 5: L l, M m, N n, O o, Ô ô, Ơ ơ"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Vần và các kết hợp vần",
                            "lessons": [
                                {"id": "b6", "title": "Bài 6: Vần có âm cuối n, t, c, ch"},
                                {"id": "b7", "title": "Bài 7: Vần có âm cuối m, p, ng, nh"},
                                {"id": "b8", "title": "Bài 8: Vần có âm đệm o, u"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Luyện đọc hiểu đoạn văn & Kể chuyện",
                            "lessons": [
                                {"id": "b9", "title": "Bài 9: Đọc hiểu văn bản truyện ngụ ngôn và cổ tích"},
                                {"id": "b10", "title": "Bài 10: Đọc hiểu văn bản thơ thiếu nhi"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tieng-anh-1",
                    "name": "Tiếng Anh 1",
                    "subject_code": "ENG",
                    "icon": "🇬🇧",
                    "chapters": [
                        {
                            "id": "u1",
                            "title": "Unit 1: In the School Playground",
                            "lessons": [
                                {"id": "l1", "title": "Lesson 1: Letter B /b/"},
                                {"id": "l2", "title": "Lesson 2: Vocabulary & Chant"},
                            ],
                        },
                        {
                            "id": "u2",
                            "title": "Unit 2: In the Dining Room",
                            "lessons": [
                                {"id": "l3", "title": "Lesson 1: Letter C /k/"},
                                {"id": "l4", "title": "Lesson 2: Vocabulary & Song"},
                            ],
                        },
                        {
                            "id": "u3",
                            "title": "Unit 3: At the Street Market",
                            "lessons": [
                                {"id": "l5", "title": "Lesson 1: Letter A /æ/"},
                                {"id": "l6", "title": "Lesson 2: Vocabulary & Practice"},
                            ],
                        },
                        {
                            "id": "u4",
                            "title": "Unit 4: In the Bedroom",
                            "lessons": [
                                {"id": "l7", "title": "Lesson 1: Letter D /d/"},
                                {"id": "l8", "title": "Lesson 2: Vocabulary & Chant"},
                            ],
                        },
                        {
                            "id": "u5",
                            "title": "Unit 5: At the Fish and Chip Shop",
                            "lessons": [
                                {"id": "l9", "title": "Lesson 1: Letter I /ɪ/"},
                                {"id": "l10", "title": "Lesson 2: Vocabulary & Practice"},
                            ],
                        },
                        {
                            "id": "u6",
                            "title": "Unit 6: In the Classroom",
                            "lessons": [
                                {"id": "l11", "title": "Lesson 1: Letter E /e/"},
                                {"id": "l12", "title": "Lesson 2: Classroom commands"},
                            ],
                        },
                        {
                            "id": "u7",
                            "title": "Unit 7: In the Garden",
                            "lessons": [
                                {"id": "l13", "title": "Lesson 1: Letter G /ɡ/"},
                                {"id": "l14", "title": "Lesson 2: Vocabulary & Practice"},
                            ],
                        },
                        {
                            "id": "u8",
                            "title": "Unit 8: In the Park",
                            "lessons": [
                                {"id": "l15", "title": "Lesson 1: Letter P /p/"},
                                {"id": "l16", "title": "Lesson 2: Action verbs"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tu-nhien-va-xa-hoi-1",
                    "name": "Tự nhiên và Xã hội 1",
                    "subject_code": "SCI",
                    "icon": "🌱",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề 1: Gia đình",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Các thành viên trong gia đình"},
                                {"id": "b2", "title": "Bài 2: Ngôi nhà của em"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề 2: Trường học",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Lớp học và trường tiểu học của em"},
                                {"id": "b4", "title": "Bài 4: Các hoạt động ở trường"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chủ đề 3: Cộng đồng địa phương",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Nơi em sống"},
                                {"id": "b6", "title": "Bài 6: An toàn trên đường đi học"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chủ đề 4: Thực vật và động vật",
                            "lessons": [
                                {"id": "b7", "title": "Bài 7: Cây xung quanh em"},
                                {"id": "b8", "title": "Bài 8: Con vật quanh em"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chủ đề 5: Con người và sức khỏe",
                            "lessons": [
                                {"id": "b9", "title": "Bài 9: Các bộ phận trên cơ thể"},
                                {"id": "b10", "title": "Bài 10: Giữ vệ sinh cá nhân và ăn uống sạch"},
                            ],
                        },
                        {
                            "id": "c6",
                            "title": "Chủ đề 6: Trái Đất và bầu trời",
                            "lessons": [
                                {"id": "b11", "title": "Bài 11: Bầu trời ban ngày và ban đêm"},
                                {"id": "b12", "title": "Bài 12: Thời tiết nắng, mưa, gió"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "dao-duc-1",
                    "name": "Đạo đức 1",
                    "subject_code": "CIVIC",
                    "icon": "⭐",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề 1: Tự chăm sóc bản thân",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Em giữ sạch đôi tay"},
                                {"id": "b2", "title": "Bài 2: Em tự mặc quần áo, đi giày dép"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề 2: Yêu thương gia đình",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Kính trọng ông bà, cha mẹ"},
                                {"id": "b4", "title": "Bài 4: Lễ phép với người lớn"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chủ đề 3: Thật thà và đúng giờ",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Thật thà trong học tập và cuộc sống"},
                                {"id": "b6", "title": "Bài 6: Đi học đúng giờ"},
                            ],
                        },
                    ],
                },
            ],
        }
    )

    # =========================================================================
    # LỚP 2
    # =========================================================================
    catalog.append(
        {
            "grade": 2,
            "subjects": [
                {
                    "slug": "toan-2",
                    "name": "Toán 2",
                    "subject_code": "MATH",
                    "icon": "📐",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Ôn tập và bổ sung phép cộng trừ trong phạm vi 20",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Bảng cộng qua 10"},
                                {"id": "b2", "title": "Bài 2: Bảng trừ qua 10"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Phép cộng, phép trừ có nhớ trong phạm vi 100",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Phép cộng có nhớ trong phạm vi 100"},
                                {"id": "b4", "title": "Bài 4: Phép trừ có nhớ trong phạm vi 100"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Khối lượng và dung tích (kg, l)",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Ki-lô-gam (kg)"},
                                {"id": "b6", "title": "Bài 6: Lít (l)"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Phép nhân và phép chia",
                            "lessons": [
                                {"id": "b7", "title": "Bài 7: Phép nhân và bảng nhân 2, 5"},
                                {"id": "b8", "title": "Bài 8: Phép chia và bảng chia 2, 5"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chương 5: Các số trong phạm vi 1000",
                            "lessons": [
                                {"id": "b9", "title": "Bài 9: Đơn vị, chục, trăm, nghìn"},
                                {"id": "b10", "title": "Bài 10: So sánh các số có ba chữ số"},
                            ],
                        },
                        {
                            "id": "c6",
                            "title": "Chương 6: Độ dài và đơn vị đo độ dài (dm, m, km)",
                            "lessons": [
                                {"id": "b11", "title": "Bài 11: Đề-xi-mét, Mét và Ki-lô-mét"},
                                {"id": "b12", "title": "Bài 12: Tính độ dài đường gấp khúc"},
                            ],
                        },
                        {
                            "id": "c7",
                            "title": "Chương 7: Phép cộng, trừ trong phạm vi 1000",
                            "lessons": [
                                {"id": "b13", "title": "Bài 13: Phép cộng không nhớ và có nhớ trong phạm vi 1000"},
                                {"id": "b14", "title": "Bài 14: Phép trừ trong phạm vi 1000"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tieng-viet-2",
                    "name": "Tiếng Việt 2",
                    "subject_code": "LIT",
                    "icon": "📖",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ điểm 1: Em lớn lên từng ngày",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Tôi là học sinh lớp 2"},
                                {"id": "b2", "title": "Bài 2: Ngày hôm qua đâu rồi?"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ điểm 2: Đi học vui sao",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Niềm vui của Bi và Bống"},
                                {"id": "b4", "title": "Bài 4: Làm việc thật là vui"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chủ điểm 3: Mái ấm gia đình",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Cầu thủ dự bị"},
                                {"id": "b6", "title": "Bài 6: Bữa cơm gia đình"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chủ điểm 4: Đất nước và con người",
                            "lessons": [
                                {"id": "b7", "title": "Bài 7: Đất nước mến yêu"},
                                {"id": "b8", "title": "Bài 8: Bác Hồ kính yêu"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tieng-anh-2",
                    "name": "Tiếng Anh 2",
                    "subject_code": "ENG",
                    "icon": "🇬🇧",
                    "chapters": [
                        {
                            "id": "u1",
                            "title": "Unit 1: At My Birthday Party",
                            "lessons": [
                                {"id": "l1", "title": "Lesson 1: Letter M /m/"},
                                {"id": "l2", "title": "Lesson 2: Numbers 11-20"},
                            ],
                        },
                        {
                            "id": "u2",
                            "title": "Unit 2: In the Backyard",
                            "lessons": [
                                {"id": "l3", "title": "Lesson 1: Letter K /k/"},
                                {"id": "l4", "title": "Lesson 2: Outdoor toys"},
                            ],
                        },
                        {
                            "id": "u3",
                            "title": "Unit 3: At the Seaside",
                            "lessons": [
                                {"id": "l5", "title": "Lesson 1: Letter S /s/"},
                                {"id": "l6", "title": "Lesson 2: Beach activities"},
                            ],
                        },
                        {
                            "id": "u4",
                            "title": "Unit 4: In the Countryside",
                            "lessons": [
                                {"id": "l7", "title": "Lesson 1: Letter H /h/"},
                                {"id": "l8", "title": "Lesson 2: Country animals"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tu-nhien-va-xa-hoi-2",
                    "name": "Tự nhiên và Xã hội 2",
                    "subject_code": "SCI",
                    "icon": "🌱",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề 1: Gia đình",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Các thế hệ trong gia đình"},
                                {"id": "b2", "title": "Bài 2: Nghề nghiệp của người lớn"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề 2: Trường học",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Ngày hội và sự kiện ở trường"},
                                {"id": "b4", "title": "Bài 4: Giữ vệ sinh trường học"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chủ đề 3: Thực vật và động vật",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Nơi sống của thực vật"},
                                {"id": "b6", "title": "Bài 6: Nơi sống của động vật"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chủ đề 4: Con người và sức khỏe",
                            "lessons": [
                                {"id": "b7", "title": "Bài 7: Cơ quan vận động"},
                                {"id": "b8", "title": "Bài 8: Cơ quan hô hấp"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "dao-duc-2",
                    "name": "Đạo đức 2",
                    "subject_code": "CIVIC",
                    "icon": "⭐",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề: Quý trọng thời gian & Nhận lỗi",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Quý trọng thời gian"},
                                {"id": "b2", "title": "Bài 2: Nhận lỗi và sửa lỗi"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề: Yêu thương con người & Bảo vệ của công",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Giúp đỡ bạn bè"},
                                {"id": "b4", "title": "Bài 4: Giữ gìn của công"},
                            ],
                        },
                    ],
                },
            ],
        }
    )

    # =========================================================================
    # LỚP 3
    # =========================================================================
    catalog.append(
        {
            "grade": 3,
            "subjects": [
                {
                    "slug": "toan-3",
                    "name": "Toán 3",
                    "subject_code": "MATH",
                    "icon": "📐",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Bảng nhân, bảng chia 3, 4, 6, 7, 8, 9",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Bảng nhân chia 3, 4"},
                                {"id": "b2", "title": "Bài 2: Bảng nhân chia 6, 7"},
                                {"id": "b3", "title": "Bài 3: Bảng nhân chia 8, 9"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Góc, tam giác, tứ giác & Hình tròn",
                            "lessons": [
                                {"id": "b4", "title": "Bài 4: Góc vuông, góc không vuông"},
                                {"id": "b5", "title": "Bài 5: Hình tròn, tâm, bán kính, đường kính"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Các số trong phạm vi 10 000 và 100 000",
                            "lessons": [
                                {"id": "b6", "title": "Bài 6: Các số trong phạm vi 10 000"},
                                {"id": "b7", "title": "Bài 7: Các số trong phạm vi 100 000"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Chu vi và diện tích (Hình vuông, chữ nhật)",
                            "lessons": [
                                {"id": "b8", "title": "Bài 8: Chu vi hình tam giác, tứ giác, chữ nhật, vuông"},
                                {"id": "b9", "title": "Bài 9: Diện tích hình chữ nhật và hình vuông"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chương 5: Bảng số liệu và khả năng xảy ra",
                            "lessons": [
                                {"id": "b10", "title": "Bài 10: Thu thập và phân loại số liệu"},
                                {"id": "b11", "title": "Bài 11: Khả năng xảy ra của một sự kiện"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tieng-viet-3",
                    "name": "Tiếng Việt 3",
                    "subject_code": "LIT",
                    "icon": "📖",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ điểm 1: Những búp măng non",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Cậu học sinh mới"},
                                {"id": "b2", "title": "Bài 2: Về thăm quê"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ điểm 2: Mái trường mến yêu",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Cánh rừng trong nắng"},
                                {"id": "b4", "title": "Bài 4: Lời giải toán đặc biệt"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chủ điểm 3: Cộng đồng gắn bó",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Tập đọc và từ ngữ chỉ hoạt động"},
                                {"id": "b6", "title": "Bài 6: Biện pháp tu từ so sánh và nhân hóa"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tieng-anh-3",
                    "name": "Tiếng Anh 3",
                    "subject_code": "ENG",
                    "icon": "🇬🇧",
                    "chapters": [
                        {
                            "id": "u1",
                            "title": "Unit 1: Hello & Greetings",
                            "lessons": [
                                {"id": "l1", "title": "Lesson 1: What's your name?"},
                                {"id": "l2", "title": "Lesson 2: How are you?"},
                            ],
                        },
                        {
                            "id": "u2",
                            "title": "Unit 2: Our Names",
                            "lessons": [
                                {"id": "l3", "title": "Lesson 1: Spelling names"},
                                {"id": "l4", "title": "Lesson 2: Practice & Project"},
                            ],
                        },
                        {
                            "id": "u3",
                            "title": "Unit 3: Our Friends",
                            "lessons": [
                                {"id": "l5", "title": "Lesson 1: This is / That is"},
                                {"id": "l6", "title": "Lesson 2: Yes, it is / No, it isn't"},
                            ],
                        },
                        {
                            "id": "u4",
                            "title": "Unit 4: Our School",
                            "lessons": [
                                {"id": "l7", "title": "Lesson 1: School facilities"},
                                {"id": "l8", "title": "Lesson 2: Is that our school?"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tu-nhien-va-xa-hoi-3",
                    "name": "Tự nhiên và Xã hội 3",
                    "subject_code": "SCI",
                    "icon": "🌱",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề 1: Gia đình và Di tích lịch sử địa phương",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Họ hàng nội ngoại"},
                                {"id": "b2", "title": "Bài 2: Di tích lịch sử văn hóa địa phương"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề 2: Thực vật và động vật",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Rễ, thân, lá của thực vật"},
                                {"id": "b4", "title": "Bài 4: Động vật trên cạn và dưới nước"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chủ đề 3: Con người và sức khỏe",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Cơ quan tuần hoàn"},
                                {"id": "b6", "title": "Bài 6: Cơ quan thần kinh"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tin-hoc-3",
                    "name": "Tin học 3",
                    "subject_code": "INFO",
                    "icon": "💻",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề A: Máy tính và em",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Thông tin và quyết định"},
                                {"id": "b2", "title": "Bài 2: Khám phá máy tính và chuột, bàn phím"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề B: Mạng máy tính và Internet",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Làm quen với Internet"},
                                {"id": "b4", "title": "Bài 4: Tìm kiếm thông tin trên Web"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chủ đề E: Ứng dụng tin học",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Tập gõ bàn phím đúng cách"},
                                {"id": "b6", "title": "Bài 6: Vẽ hình đơn giản trên máy tính"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "cong-nghe-3",
                    "name": "Công nghệ 3",
                    "subject_code": "TECH",
                    "icon": "🛠️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Phần 1: Công nghệ và đời sống",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Tự nhiên và công nghệ"},
                                {"id": "b2", "title": "Bài 2: Đèn học và quạt điện"},
                                {"id": "b3", "title": "Bài 3: An toàn khi sử dụng đồ điện"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Phần 2: Thủ công kĩ thuật",
                            "lessons": [
                                {"id": "b4", "title": "Bài 4: Làm đồ dùng học tập"},
                                {"id": "b5", "title": "Bài 5: Làm đồ chơi đơn giản"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "dao-duc-3",
                    "name": "Đạo đức 3",
                    "subject_code": "CIVIC",
                    "icon": "⭐",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề: Tôn trọng sự khác biệt & Giữ lời hứa",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Tôn trọng bạn bè"},
                                {"id": "b2", "title": "Bài 2: Giữ lời hứa"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề: Khám phá quê hương & Bảo vệ môi trường",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Yêu mến quê hương"},
                                {"id": "b4", "title": "Bài 4: Tiết kiệm điện nước"},
                            ],
                        },
                    ],
                },
            ],
        }
    )

    # =========================================================================
    # LỚP 4
    # =========================================================================
    catalog.append(
        {
            "grade": 4,
            "subjects": [
                {
                    "slug": "toan-4",
                    "name": "Toán 4",
                    "subject_code": "MATH",
                    "icon": "📐",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Số tự nhiên và các phép tính",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Lớp nghìn, lớp triệu"},
                                {"id": "b2", "title": "Bài 2: Phép cộng, phép trừ số tự nhiên"},
                                {"id": "b3", "title": "Bài 3: Phép nhân và phép chia số tự nhiên"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Phân số và các phép tính phân số",
                            "lessons": [
                                {"id": "b4", "title": "Bài 4: Khái niệm phân số, tính chất cơ bản"},
                                {"id": "b5", "title": "Bài 5: Cộng trừ phân số"},
                                {"id": "b6", "title": "Bài 6: Nhân chia phân số"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Hình học và Đo lường",
                            "lessons": [
                                {"id": "b7", "title": "Bài 7: Góc nhọn, góc tù, góc bẹt"},
                                {"id": "b8", "title": "Bài 8: Hai đường thẳng vuông góc, hai đường thẳng song song"},
                                {"id": "b9", "title": "Bài 9: Hình bình hành và hình thoi"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Thống kê và xác suất",
                            "lessons": [
                                {"id": "b10", "title": "Bài 10: Dãy số liệu thống kê và biểu đồ cột"},
                                {"id": "b11", "title": "Bài 11: Số lần xuất hiện của một sự kiện"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tieng-viet-4",
                    "name": "Tiếng Việt 4",
                    "subject_code": "LIT",
                    "icon": "📖",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ điểm 1: Khám phá thế giới tuổi thơ",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Cánh diều tuổi thơ"},
                                {"id": "b2", "title": "Bài 2: Luyện từ và câu: Danh từ, Động từ, Tính từ"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ điểm 2: Chắp cánh ước mơ",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Những hạt thóc giống"},
                                {"id": "b4", "title": "Bài 4: Viết đoạn văn miêu tả cây cối, con vật"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tieng-anh-4",
                    "name": "Tiếng Anh 4",
                    "subject_code": "ENG",
                    "icon": "🇬🇧",
                    "chapters": [
                        {
                            "id": "u1",
                            "title": "Unit 1: My Friends & Time",
                            "lessons": [
                                {"id": "l1", "title": "Lesson 1: Where are you from?"},
                                {"id": "l2", "title": "Lesson 2: Nationalities"},
                            ],
                        },
                        {
                            "id": "u2",
                            "title": "Unit 2: Time and Daily Routines",
                            "lessons": [
                                {"id": "l3", "title": "Lesson 1: What time is it?"},
                                {"id": "l4", "title": "Lesson 2: Daily habits"},
                            ],
                        },
                        {
                            "id": "u3",
                            "title": "Unit 3: My Week",
                            "lessons": [
                                {"id": "l5", "title": "Lesson 1: What day is it today?"},
                                {"id": "l6", "title": "Lesson 2: School subjects"},
                            ],
                        },
                        {
                            "id": "u4",
                            "title": "Unit 4: My Birthday",
                            "lessons": [
                                {"id": "l7", "title": "Lesson 1: When is your birthday?"},
                                {"id": "l8", "title": "Lesson 2: Months of the year"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "khoa-hoc-4",
                    "name": "Khoa học 4",
                    "subject_code": "SCI",
                    "icon": "🔬",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề 1: Chất (Nước và Không khí)",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Nước có ở đâu, tính chất và vòng tuần hoàn của nước"},
                                {"id": "b2", "title": "Bài 2: Thành phần và vai trò của không khí"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề 2: Năng lượng (Ánh sáng, Âm thanh, Nhiệt)",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Ánh sáng và vai trò đối với sự sống"},
                                {"id": "b4", "title": "Bài 4: Âm thanh và nhiệt kế, sự truyền nhiệt"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chủ đề 3: Thực vật và động vật",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Sự trao đổi chất ở thực vật và động vật"},
                                {"id": "b6", "title": "Bài 6: Chuỗi thức ăn trong tự nhiên"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chủ đề 4: Nấm và Vi khuẩn",
                            "lessons": [
                                {"id": "b7", "title": "Bài 7: Nấm ăn và nấm men"},
                                {"id": "b8", "title": "Bài 8: Vi khuẩn có ích và vi khuẩn gây bệnh"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "lich-su-va-dia-li-4",
                    "name": "Lịch sử và Địa lí 4",
                    "subject_code": "HIST_GEO",
                    "icon": "🗺️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề 1: Địa phương em",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Thiên nhiên và con người địa phương"},
                                {"id": "b2", "title": "Bài 2: Lịch sử và danh nhân quê hương"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề 2: Vùng Trung du và miền núi Bắc Bộ",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Thiên nhiên và đời sống vùng Trung du miền núi Bắc Bộ"},
                                {"id": "b4", "title": "Bài 4: Đền Hùng và Lễ hội Đền Hùng"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chủ đề 3: Vùng Đồng bằng Bắc Bộ",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Thiên nhiên và dân cư vùng Đồng bằng Bắc Bộ"},
                                {"id": "b6", "title": "Bài 6: Thăng Long - Hà Nội"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chủ đề 4: Vùng Duyên hải miền Trung",
                            "lessons": [
                                {"id": "b7", "title": "Bài 7: Thiên nhiên miền Trung"},
                                {"id": "b8", "title": "Bài 8: Cố đô Huế và Phố cổ Hội An"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chủ đề 5: Vùng Tây Nguyên và Nam Bộ",
                            "lessons": [
                                {"id": "b9", "title": "Bài 9: Không gian văn hóa Cồng chiêng Tây Nguyên"},
                                {"id": "b10", "title": "Bài 10: Thành phố Hồ Chí Minh và Địa đạo Củ Chi"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tin-hoc-4",
                    "name": "Tin học 4",
                    "subject_code": "INFO",
                    "icon": "💻",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề: Máy tính và cộng đồng",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Phần cứng và phần mềm máy tính"},
                                {"id": "b2", "title": "Bài 2: Gõ bàn phím tiếng Việt (Unikey)"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề: Soạn thảo văn bản và Trình chiếu",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Soạn thảo văn bản Word cơ bản"},
                                {"id": "b4", "title": "Bài 4: Tạo bài trình chiếu PowerPoint đơn giản"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "cong-nghe-4",
                    "name": "Công nghệ 4",
                    "subject_code": "TECH",
                    "icon": "🛠️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Phần 1: Công nghệ và đời sống",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Hoa và cây cảnh quanh em"},
                                {"id": "b2", "title": "Bài 2: Trồng và chăm sóc cây cảnh"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Phần 2: Thủ công kĩ thuật",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Lắp ghép mô hình kĩ thuật"},
                                {"id": "b4", "title": "Bài 4: Làm chuồn chuồn thăng bằng"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "dao-duc-4",
                    "name": "Đạo đức 4",
                    "subject_code": "CIVIC",
                    "icon": "⭐",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề: Biết ơn thầy cô & Bảo vệ môi trường",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Biết ơn thầy cô giáo"},
                                {"id": "b2", "title": "Bài 2: Giữ gìn vệ sinh môi trường sống"},
                            ],
                        },
                    ],
                },
            ],
        }
    )

    # =========================================================================
    # LỚP 5
    # =========================================================================
    catalog.append(
        {
            "grade": 5,
            "subjects": [
                {
                    "slug": "toan-5",
                    "name": "Toán 5",
                    "subject_code": "MATH",
                    "icon": "📐",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Ôn tập và bổ sung về Phân số",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Khái niệm số thập phân"},
                                {"id": "b2", "title": "Bài 2: Hàng của số thập phân và so sánh số thập phân"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Các phép tính với số thập phân",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Cộng, trừ số thập phân"},
                                {"id": "b4", "title": "Bài 4: Nhân số thập phân"},
                                {"id": "b5", "title": "Bài 5: Chia số thập phân"},
                                {"id": "b6", "title": "Bài 6: Tỉ số phần trăm và bài toán tỉ số phần trăm"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Hình học (Tam giác, Thang, Tròn, Hộp chữ nhật, Lập phương)",
                            "lessons": [
                                {"id": "b7", "title": "Bài 7: Diện tích hình tam giác và hình thang"},
                                {"id": "b8", "title": "Bài 8: Chu vi và diện tích hình tròn"},
                                {
                                    "id": "b9",
                                    "title": "Bài 9: Diện tích xung quanh, toàn phần và thể tích hình hộp chữ nhật, lập phương",
                                },
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Số đo thời gian & Toán chuyển động đều",
                            "lessons": [
                                {"id": "b10", "title": "Bài 10: Cộng, trừ, nhân, chia số đo thời gian"},
                                {"id": "b11", "title": "Bài 11: Vận tốc, quãng đường, thời gian (v, s, t)"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tieng-viet-5",
                    "name": "Tiếng Việt 5",
                    "subject_code": "LIT",
                    "icon": "📖",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ điểm 1: Việt Nam - Tổ quốc em",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Thư gửi các học sinh"},
                                {"id": "b2", "title": "Bài 2: Quang cảnh làng mạc ngày mùa"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ điểm 2: Cánh chim hòa bình",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Bài ca về trái đất"},
                                {
                                    "id": "b4",
                                    "title": "Bài 4: Luyện từ và câu: Từ đồng nghĩa, từ trái nghĩa, từ nhiều nghĩa",
                                },
                            ],
                        },
                    ],
                },
                {
                    "slug": "tieng-anh-5",
                    "name": "Tiếng Anh 5",
                    "subject_code": "ENG",
                    "icon": "🇬🇧",
                    "chapters": [
                        {
                            "id": "u1",
                            "title": "Unit 1: All About Me",
                            "lessons": [
                                {"id": "l1", "title": "Lesson 1: What's your address?"},
                                {"id": "l2", "title": "Lesson 2: What's your city like?"},
                            ],
                        },
                        {
                            "id": "u2",
                            "title": "Unit 2: Our Free-time Activities",
                            "lessons": [
                                {"id": "l3", "title": "Lesson 1: How often do you study?"},
                                {"id": "l4", "title": "Lesson 2: Free time hobbies"},
                            ],
                        },
                        {
                            "id": "u3",
                            "title": "Unit 3: My Foreign Friends",
                            "lessons": [
                                {"id": "l5", "title": "Lesson 1: Where did you go on holiday?"},
                                {"id": "l6", "title": "Lesson 2: Means of transport"},
                            ],
                        },
                        {
                            "id": "u4",
                            "title": "Unit 4: Our Future Jobs",
                            "lessons": [
                                {"id": "l7", "title": "Lesson 1: What would you like to be?"},
                                {"id": "l8", "title": "Lesson 2: Why do you want to be a doctor/teacher?"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "khoa-hoc-5",
                    "name": "Khoa học 5",
                    "subject_code": "SCI",
                    "icon": "🔬",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề 1: Chất",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Sự biến đổi của chất (biến đổi hóa học, vật lí)"},
                                {"id": "b2", "title": "Bài 2: Kim loại và phi kim trong đời sống"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề 2: Năng lượng",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Năng lượng mặt trời, gió và nước chảy"},
                                {"id": "b4", "title": "Bài 4: Sử dụng năng lượng điện an toàn và tiết kiệm"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chủ đề 3: Thực vật và động vật",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Sự sinh sản của thực vật có hoa"},
                                {"id": "b6", "title": "Bài 6: Sự sinh sản và nuôi con của động vật"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chủ đề 4: Con người và môi trường",
                            "lessons": [
                                {"id": "b7", "title": "Bài 7: Môi trường và tài nguyên thiên nhiên"},
                                {"id": "b8", "title": "Bài 8: Tác động của con người đến môi trường"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "lich-su-va-dia-li-5",
                    "name": "Lịch sử và Địa lí 5",
                    "subject_code": "HIST_GEO",
                    "icon": "🗺️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề 1: Đất nước và con người Việt Nam",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Vị trí địa lí, địa hình và khoáng sản VN"},
                                {"id": "b2", "title": "Bài 2: Khí hậu, sông ngòi và biển đảo VN"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề 2: Những mốc son lịch sử vẻ vang",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Cách mạng tháng Tám 1945 và Tuyên ngôn Độc lập"},
                                {"id": "b4", "title": "Bài 4: Chiến thắng Điện Biên Phủ 1954"},
                                {"id": "b5", "title": "Bài 5: Chiến dịch Hồ Chí Minh lịch sử 1975"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chủ đề 3: Các nước láng giềng và các châu lục",
                            "lessons": [
                                {"id": "b6", "title": "Bài 6: Các nước láng giềng: Lào, Cam-pu-chia, Trung Quốc"},
                                {"id": "b7", "title": "Bài 7: Châu Á, Châu Âu, Châu Phi, Châu Mỹ, Châu Đại Dương"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tin-hoc-5",
                    "name": "Tin học 5",
                    "subject_code": "INFO",
                    "icon": "💻",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề: Máy tính và cuộc sống",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Sử dụng thư mục và tệp hiệu quả"},
                                {"id": "b2", "title": "Bài 2: An toàn thông tin trên mạng"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề: Lập trình Scratch đơn giản",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Làm quen với ngôn ngữ lập trình trực quan Scratch"},
                                {"id": "b4", "title": "Bài 4: Tạo nhân vật và chuyển động trong Scratch"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "cong-nghe-5",
                    "name": "Công nghệ 5",
                    "subject_code": "TECH",
                    "icon": "🛠️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Phần 1: Công nghệ và đời sống",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Sử dụng điện thoại an toàn"},
                                {"id": "b2", "title": "Bài 2: Lắp mạch điện đơn giản với pin và bóng đèn"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Phần 2: Thủ công kĩ thuật",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Lắp ráp mô hình xe chạy bằng pin"},
                                {"id": "b4", "title": "Bài 4: Lắp ráp đồ chơi điều khiển từ xa"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "dao-duc-5",
                    "name": "Đạo đức 5",
                    "subject_code": "CIVIC",
                    "icon": "⭐",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề: Tình bạn, Tình thầy trò & Lòng biết ơn",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Em là học sinh lớp 5"},
                                {"id": "b2", "title": "Bài 2: Tình bạn thân thiết và chia sẻ"},
                            ],
                        },
                    ],
                },
            ],
        }
    )

    # =========================================================================
    # LỚP 6
    # =========================================================================
    catalog.append(
        {
            "grade": 6,
            "subjects": [
                {
                    "slug": "toan-6",
                    "name": "Toán 6",
                    "subject_code": "MATH",
                    "icon": "📐",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Tập hợp các số tự nhiên",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Tập hợp"},
                                {"id": "b2", "title": "Bài 2: Cách ghi số tự nhiên"},
                                {"id": "b3", "title": "Bài 3: Thứ tự trong tập hợp các số tự nhiên"},
                                {"id": "b4", "title": "Bài 4: Phép cộng và phép trừ số tự nhiên"},
                                {"id": "b5", "title": "Bài 5: Phép nhân và phép chia số tự nhiên"},
                                {"id": "b6", "title": "Bài 6: Lũy thừa với số mũ tự nhiên"},
                                {"id": "b7", "title": "Bài 7: Thứ tự thực hiện các phép tính"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Tính chia hết trong tập hợp các số tự nhiên",
                            "lessons": [
                                {"id": "b8", "title": "Bài 8: Quan hệ chia hết và tính chất"},
                                {"id": "b9", "title": "Bài 9: Dấu hiệu chia hết cho 2, cho 5"},
                                {"id": "b10", "title": "Bài 10: Dấu hiệu chia hết cho 3, cho 9"},
                                {"id": "b11", "title": "Bài 11: Số nguyên tố, hợp số"},
                                {"id": "b12", "title": "Bài 12: Ước chung và ước chung lớn nhất (ƯCLN)"},
                                {"id": "b13", "title": "Bài 13: Bội chung và bội chung nhỏ nhất (BCNN)"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Số nguyên",
                            "lessons": [
                                {"id": "b14", "title": "Bài 14: Tập hợp các số nguyên"},
                                {"id": "b15", "title": "Bài 15: Phép cộng và phép trừ số nguyên"},
                                {"id": "b16", "title": "Bài 16: Quy tắc dấu ngoặc"},
                                {"id": "b17", "title": "Bài 17: Phép nhân và phép chia hết hai số nguyên"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Một số hình phẳng trong thực tiễn",
                            "lessons": [
                                {"id": "b18", "title": "Bài 18: Tam giác đều, hình vuông, lục giác đều"},
                                {
                                    "id": "b19",
                                    "title": "Bài 19: Hình chữ nhật, hình thoi, hình bình hành, hình thang cân",
                                },
                                {"id": "b20", "title": "Bài 20: Chu vi và diện tích một số tứ giác đã học"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chương 5: Phân số",
                            "lessons": [
                                {"id": "b21", "title": "Bài 21: Mở rộng phân số, phân số bằng nhau"},
                                {"id": "b22", "title": "Bài 22: So sánh phân số, hỗn số dương"},
                                {"id": "b23", "title": "Bài 23: Phép cộng và phép trừ phân số"},
                                {"id": "b24", "title": "Bài 24: Phép nhân và phép chia phân số"},
                                {"id": "b25", "title": "Bài 25: Hai bài toán về phân số"},
                            ],
                        },
                        {
                            "id": "c6",
                            "title": "Chương 6: Số thập phân",
                            "lessons": [
                                {"id": "b26", "title": "Bài 26: Số thập phân"},
                                {"id": "b27", "title": "Bài 27: Các phép tính với số thập phân"},
                                {"id": "b28", "title": "Bài 28: Tỉ số và tỉ số phần trăm"},
                                {"id": "b29", "title": "Bài 29: Làm tròn số và ước lượng kết quả"},
                            ],
                        },
                        {
                            "id": "c7",
                            "title": "Chương 7: Những hình hình học cơ bản",
                            "lessons": [
                                {"id": "b30", "title": "Bài 30: Điểm và đường thẳng"},
                                {"id": "b31", "title": "Bài 31: Điểm nằm giữa hai điểm, tia"},
                                {"id": "b32", "title": "Bài 32: Đoạn thẳng, độ dài đoạn thẳng"},
                                {"id": "b33", "title": "Bài 33: Trung điểm của đoạn thẳng"},
                                {"id": "b34", "title": "Bài 34: Góc và số đo góc"},
                            ],
                        },
                        {
                            "id": "c8",
                            "title": "Chương 8: Dữ liệu và xác suất thực nghiệm",
                            "lessons": [
                                {"id": "b35", "title": "Bài 35: Dữ liệu và thu thập dữ liệu"},
                                {"id": "b36", "title": "Bài 36: Bảng thống kê và biểu đồ tranh"},
                                {"id": "b37", "title": "Bài 37: Biểu đồ cột và biểu đồ cột kép"},
                                {"id": "b38", "title": "Bài 38: Xác suất thực nghiệm"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "ngu-van-6",
                    "name": "Ngữ văn 6",
                    "subject_code": "LIT",
                    "icon": "📖",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Bài 1: Tôi và các bạn (Truyện đồng thoại)",
                            "lessons": [
                                {"id": "b1", "title": "Văn bản 1: Bài học đường đời đầu tiên"},
                                {"id": "b2", "title": "Văn bản 2: Nếu cậu muốn có một người bạn"},
                                {"id": "b3", "title": "Thực hành tiếng Việt: Từ đơn và từ phức"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Bài 2: Gõ cửa trái tim (Thơ lục bát)",
                            "lessons": [
                                {"id": "b4", "title": "Văn bản 1: Chuyện cổ tích về loài người"},
                                {"id": "b5", "title": "Văn bản 2: Mây và sóng"},
                                {"id": "b6", "title": "Thực hành tiếng Việt: Biện pháp tu từ ẩn dụ, điệp ngữ"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Bài 3: Yêu thương và chia sẻ (Truyện ngắn)",
                            "lessons": [
                                {"id": "b7", "title": "Văn bản 1: Cô bé bán diêm"},
                                {"id": "b8", "title": "Văn bản 2: Gió lạnh đầu mùa"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Bài 4: Quê hương yêu dấu (Thơ)",
                            "lessons": [
                                {"id": "b9", "title": "Văn bản 1: Chùm ca dao về quê hương đất nước"},
                                {"id": "b10", "title": "Văn bản 2: Hoa bìm"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Bài 5: Những nẻo đường xứ sở (Kí)",
                            "lessons": [
                                {"id": "b11", "title": "Văn bản 1: Cô Tô"},
                                {"id": "b12", "title": "Văn bản 2: Hang Én"},
                            ],
                        },
                        {
                            "id": "c6",
                            "title": "Bài 6: Chuyện kể về những người anh hùng (Truyền thuyết)",
                            "lessons": [
                                {"id": "b13", "title": "Văn bản 1: Thánh Gióng"},
                                {"id": "b14", "title": "Văn bản 2: Sơn Tinh, Thủy Tinh"},
                            ],
                        },
                        {
                            "id": "c7",
                            "title": "Bài 7: Thế giới cổ tích (Cổ tích)",
                            "lessons": [
                                {"id": "b15", "title": "Văn bản 1: Thạch Sanh"},
                                {"id": "b16", "title": "Văn bản 2: Cây khế"},
                            ],
                        },
                        {
                            "id": "c8",
                            "title": "Bài 8: Khác biệt và gần gũi (Nghị luận)",
                            "lessons": [
                                {"id": "b17", "title": "Văn bản 1: Xem người ta kìa!"},
                                {"id": "b18", "title": "Văn bản 2: Hai loại khác biệt"},
                            ],
                        },
                        {
                            "id": "c9",
                            "title": "Bài 9: Trái Đất - ngôi nhà chung (Thông tin)",
                            "lessons": [
                                {"id": "b19", "title": "Văn bản 1: Trái Đất - cái nôi của sự sống"},
                                {"id": "b20", "title": "Văn bản 2: Các loài tuyệt chủng"},
                            ],
                        },
                        {
                            "id": "c10",
                            "title": "Bài 10: Cuốn sách tôi yêu (Đọc mở rộng)",
                            "lessons": [{"id": "b21", "title": "Đọc mở rộng và tóm tắt tác phẩm tự chọn"}],
                        },
                    ],
                },
                {
                    "slug": "tieng-anh-6",
                    "name": "Tiếng Anh 6",
                    "subject_code": "ENG",
                    "icon": "🇬🇧",
                    "chapters": [
                        {
                            "id": "u1",
                            "title": "Unit 1: My New School",
                            "lessons": [
                                {"id": "l1", "title": "Getting Started & Vocabulary"},
                                {"id": "l2", "title": "A Closer Look 1 & 2: Present Simple"},
                                {"id": "l3", "title": "Skills 1, 2 & Looking Back"},
                            ],
                        },
                        {
                            "id": "u2",
                            "title": "Unit 2: My House",
                            "lessons": [
                                {"id": "l4", "title": "Types of houses & Rooms"},
                                {"id": "l5", "title": "Prepositions of place & Possessive 's"},
                            ],
                        },
                        {
                            "id": "u3",
                            "title": "Unit 3: My Friends",
                            "lessons": [
                                {"id": "l6", "title": "Personality adjectives & Body parts"},
                                {"id": "l7", "title": "Present Continuous tense"},
                            ],
                        },
                        {
                            "id": "u4",
                            "title": "Unit 4: My Neighbourhood",
                            "lessons": [
                                {"id": "l8", "title": "Places in neighbourhood"},
                                {"id": "l9", "title": "Comparative adjectives (Short & Long)"},
                            ],
                        },
                        {
                            "id": "u5",
                            "title": "Unit 5: Natural Wonders of Viet Nam",
                            "lessons": [
                                {"id": "l10", "title": "Geography & Travel items"},
                                {"id": "l11", "title": "Modal verb: Must / Mustn't"},
                            ],
                        },
                        {
                            "id": "u6",
                            "title": "Unit 6: Our Tet Holiday",
                            "lessons": [
                                {"id": "l12", "title": "Tet customs & Traditions"},
                                {"id": "l13", "title": "Should / Shouldn't"},
                            ],
                        },
                        {
                            "id": "u7",
                            "title": "Unit 7: Television",
                            "lessons": [
                                {"id": "l14", "title": "TV programmes"},
                                {"id": "l15", "title": "Wh-questions & Conjunctions (and, but, so)"},
                            ],
                        },
                        {
                            "id": "u8",
                            "title": "Unit 8: Sports and Games",
                            "lessons": [
                                {"id": "l16", "title": "Types of sports"},
                                {"id": "l17", "title": "Past Simple tense"},
                            ],
                        },
                        {
                            "id": "u9",
                            "title": "Unit 9: Cities of the World",
                            "lessons": [
                                {"id": "l18", "title": "City landmarks"},
                                {"id": "l19", "title": "Possessive pronouns"},
                            ],
                        },
                        {
                            "id": "u10",
                            "title": "Unit 10: Our Houses in the Future",
                            "lessons": [
                                {"id": "l20", "title": "Future appliances"},
                                {"id": "l21", "title": "Will + V for future prediction"},
                            ],
                        },
                        {
                            "id": "u11",
                            "title": "Unit 11: Our Greener World",
                            "lessons": [
                                {"id": "l22", "title": "3Rs: Reduce, Reuse, Recycle"},
                                {"id": "l23", "title": "First conditional: If + Present, Will"},
                            ],
                        },
                        {
                            "id": "u12",
                            "title": "Unit 12: Robots",
                            "lessons": [
                                {"id": "l24", "title": "Types of robots & Abilities"},
                                {"id": "l25", "title": "Superlative adjectives"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "khoa-hoc-tu-nhien-6",
                    "name": "Khoa học tự nhiên 6",
                    "subject_code": "SCI",
                    "icon": "🔬",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Mở đầu về KHTN & Đo lường",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Giới thiệu về KHTN"},
                                {"id": "b2", "title": "Bài 2: An toàn trong phòng thực hành"},
                                {"id": "b3", "title": "Bài 3: Đo chiều dài, khối lượng và thời gian"},
                                {"id": "b4", "title": "Bài 4: Đo nhiệt độ và thang nhiệt độ Celsius"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Chất quanh ta",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Các thể của chất và sự chuyển thể"},
                                {"id": "b6", "title": "Bài 6: Tính chất của chất"},
                                {"id": "b7", "title": "Bài 7: Oxygen và không khí"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Vật liệu, nhiên liệu, nguyên liệu thông dụng",
                            "lessons": [
                                {"id": "b8", "title": "Bài 8: Một số vật liệu thông dụng"},
                                {"id": "b9", "title": "Bài 9: Một số nhiên liệu và nguyên liệu"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Hỗn hợp và chất tinh khiết",
                            "lessons": [
                                {"id": "b10", "title": "Bài 10: Dung dịch, huyền phù, nhũ tương"},
                                {"id": "b11", "title": "Bài 11: Tách chất ra khỏi hỗn hợp"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chương 5: Tế bào - Đơn vị cơ sở của sự sống",
                            "lessons": [
                                {"id": "b12", "title": "Bài 12: Tế bào: Đơn vị cơ sở của sự sống"},
                                {"id": "b13", "title": "Bài 13: Cấu tạo và chức năng các thành phần tế bào"},
                                {"id": "b14", "title": "Bài 14: Sự lớn lên và sinh sản của tế bào"},
                            ],
                        },
                        {
                            "id": "c6",
                            "title": "Chương 6: Từ tế bào đến cơ thể",
                            "lessons": [
                                {"id": "b15", "title": "Bài 15: Cơ thể đơn bào và cơ thể đa bào"},
                                {"id": "b16", "title": "Bài 16: Các cấp độ tổ chức trong cơ thể đa bào"},
                            ],
                        },
                        {
                            "id": "c7",
                            "title": "Chương 7: Đa dạng thế giới sống",
                            "lessons": [
                                {"id": "b17", "title": "Bài 17: Phân loại thế giới sống"},
                                {"id": "b18", "title": "Bài 18: Khóa lưỡng phân"},
                                {"id": "b19", "title": "Bài 19: Virus và vi khuẩn"},
                                {"id": "b20", "title": "Bài 20: Nấm và giới Thực vật"},
                                {"id": "b21", "title": "Bài 21: Giới Động vật và bảo vệ đa dạng sinh học"},
                            ],
                        },
                        {
                            "id": "c8",
                            "title": "Chương 8: Lực trong đời sống",
                            "lessons": [
                                {"id": "b22", "title": "Bài 22: Lực và biểu diễn lực"},
                                {"id": "b23", "title": "Bài 23: Tác dụng của lực: Biến dạng và biến đổi chuyển động"},
                                {"id": "b24", "title": "Bài 24: Lực tiếp xúc và lực không tiếp xúc"},
                                {"id": "b25", "title": "Bài 25: Lực ma sát và lực cản của nước"},
                            ],
                        },
                        {
                            "id": "c9",
                            "title": "Chương 9: Năng lượng và sự biến đổi",
                            "lessons": [
                                {"id": "b26", "title": "Bài 26: Năng lượng và các dạng năng lượng"},
                                {"id": "b27", "title": "Bài 27: Sự truyền năng lượng và định luật bảo toàn năng lượng"},
                                {"id": "b28", "title": "Bài 28: Năng lượng tái tạo và năng lượng hóa thạch"},
                            ],
                        },
                        {
                            "id": "c10",
                            "title": "Chương 10: Trái Đất và bầu trời",
                            "lessons": [
                                {"id": "b29", "title": "Bài 29: Chuyển động nhìn thấy của Mặt Trời và Mặt Trăng"},
                                {"id": "b30", "title": "Bài 30: Hệ Mặt Trời và Ngân Hà"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "lich-su-va-dia-li-6",
                    "name": "Lịch sử và Địa lí 6",
                    "subject_code": "HIST_GEO",
                    "icon": "🗺️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Lịch sử: Xã hội nguyên thủy & Các nền văn minh cổ đại",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Nguồn gốc loài người"},
                                {"id": "b2", "title": "Bài 2: Ai Cập và Lưỡng Hà cổ đại"},
                                {"id": "b3", "title": "Bài 3: Ấn Độ và Trung Quốc cổ đại"},
                                {"id": "b4", "title": "Bài 4: Hy Lạp và La Mã cổ đại"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Lịch sử: Đông Nam Á và Việt Nam từ thời dựng nước",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Các vương quốc cổ Đông Nam Á"},
                                {"id": "b6", "title": "Bài 6: Nhà nước Văn Lang, Âu Lạc"},
                                {"id": "b7", "title": "Bài 7: Đấu tranh giành độc lập thời Bắc thuộc"},
                                {"id": "b8", "title": "Bài 8: Khởi nghĩa Hai Bà Trưng, Lý Bí, Mai Thúc Loan"},
                                {"id": "b9", "title": "Bài 9: Chiến thắng Bạch Đằng năm 938"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Địa lí: Bản đồ và Trái Đất",
                            "lessons": [
                                {"id": "b10", "title": "Bài 10: Hệ thống kinh vĩ tuyến và tọa độ địa lí"},
                                {"id": "b11", "title": "Bài 11: Bản đồ và tỉ lệ bản đồ"},
                                {
                                    "id": "b12",
                                    "title": "Bài 12: Chuyển động tự quay quanh trục và quay quanh Mặt Trời của Trái Đất",
                                },
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Địa lí: Các thành phần tự nhiên của Trái Đất",
                            "lessons": [
                                {"id": "b13", "title": "Bài 13: Cấu tạo Trái Đất và núi lửa, động đất"},
                                {"id": "b14", "title": "Bài 14: Lớp vỏ khí, khí áp và gió"},
                                {"id": "b15", "title": "Bài 15: Nhiệt độ không khí, mây và mưa"},
                                {"id": "b16", "title": "Bài 16: Nước trên Trái Đất, sông và hồ"},
                                {"id": "b17", "title": "Bài 17: Đất và sinh vật trên Trái Đất"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tin-hoc-6",
                    "name": "Tin học 6",
                    "subject_code": "INFO",
                    "icon": "💻",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề 1: Máy tính và cộng đồng",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Thông tin và dữ liệu"},
                                {"id": "b2", "title": "Bài 2: Xử lí thông tin"},
                                {"id": "b3", "title": "Bài 3: Thông tin trong máy tính: Bit và Byte"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề 2: Mạng máy tính và Internet",
                            "lessons": [
                                {"id": "b4", "title": "Bài 4: Mạng máy tính và các thành phần"},
                                {"id": "b5", "title": "Bài 5: Internet và World Wide Web"},
                                {"id": "b6", "title": "Bài 6: Thư điện tử (Email)"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chủ đề 3: Tổ chức lưu trữ và tìm kiếm thông tin",
                            "lessons": [
                                {"id": "b7", "title": "Bài 7: Tìm kiếm thông tin trên Internet"},
                                {"id": "b8", "title": "Bài 8: Sơ đồ tư duy"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chủ đề 4: Đạo đức, pháp luật và văn hóa trong môi trường số",
                            "lessons": [{"id": "b9", "title": "Bài 9: Bản quyền và an toàn thông tin cá nhân"}],
                        },
                        {
                            "id": "c5",
                            "title": "Chủ đề 5: Ứng dụng tin học: Soạn thảo và Trình chiếu",
                            "lessons": [
                                {"id": "b10", "title": "Bài 10: Định dạng văn bản và chèn hình ảnh"},
                                {"id": "b11", "title": "Bài 11: Tạo bài trình chiếu với hiệu ứng"},
                            ],
                        },
                        {
                            "id": "c6",
                            "title": "Chủ đề 6: Thuật toán và Giải quyết vấn đề",
                            "lessons": [
                                {"id": "b12", "title": "Bài 12: Khái niệm thuật toán"},
                                {"id": "b13", "title": "Bài 13: Mô tả thuật toán bằng sơ đồ khối"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "cong-nghe-6",
                    "name": "Công nghệ 6",
                    "subject_code": "TECH",
                    "icon": "🛠️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Nhà ở",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Khái quát về nhà ở"},
                                {"id": "b2", "title": "Bài 2: Xây dựng nhà ở"},
                                {"id": "b3", "title": "Bài 3: Ngôi nhà thông minh"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Bảo quản và chế biến thực phẩm",
                            "lessons": [
                                {"id": "b4", "title": "Bài 4: Thực phẩm và dinh dưỡng"},
                                {"id": "b5", "title": "Bài 5: Phương pháp bảo quản thực phẩm"},
                                {"id": "b6", "title": "Bài 6: Phương pháp chế biến thực phẩm"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Trang phục và thời trang",
                            "lessons": [
                                {"id": "b7", "title": "Bài 7: Trang phục trong đời sống"},
                                {"id": "b8", "title": "Bài 8: Sử dụng và bảo quản trang phục"},
                                {"id": "b9", "title": "Bài 9: Thời trang"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Đồ dùng điện trong gia đình",
                            "lessons": [
                                {"id": "b10", "title": "Bài 10: Khái quát về đồ dùng điện"},
                                {"id": "b11", "title": "Bài 11: Đèn điện, quạt điện và nồi cơm điện"},
                                {"id": "b12", "title": "Bài 12: Tiết kiệm năng lượng trong gia đình"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "giao-duc-cong-dan-6",
                    "name": "Giáo dục công dân 6",
                    "subject_code": "CIVIC",
                    "icon": "⚖️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Đạo đức và lối sống",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Tự hào về truyền thống gia đình, dòng họ"},
                                {"id": "b2", "title": "Bài 2: Yêu thương con người"},
                                {"id": "b3", "title": "Bài 3: Siêng năng, kiên trì"},
                                {"id": "b4", "title": "Bài 4: Tôn trọng sự thật"},
                                {"id": "b5", "title": "Bài 5: Tự lập"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Kĩ năng sống và Pháp luật",
                            "lessons": [
                                {"id": "b6", "title": "Bài 6: Tự nhận thức bản thân"},
                                {"id": "b7", "title": "Bài 7: Ứng phó với các tình huống nguy hiểm"},
                                {"id": "b8", "title": "Bài 8: Tiết kiệm"},
                                {"id": "b9", "title": "Bài 9: Công dân nước CHXHCN Việt Nam"},
                                {"id": "b10", "title": "Bài 10: Quyền và nghĩa vụ cơ bản của công dân"},
                            ],
                        },
                    ],
                },
            ],
        }
    )

    # =========================================================================
    # LỚP 7
    # =========================================================================
    catalog.append(
        {
            "grade": 7,
            "subjects": [
                {
                    "slug": "toan-7",
                    "name": "Toán 7",
                    "subject_code": "MATH",
                    "icon": "📐",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Số hữu tỉ",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Tập hợp các số hữu tỉ"},
                                {"id": "b2", "title": "Bài 2: Cộng, trừ, nhân, chia số hữu tỉ"},
                                {"id": "b3", "title": "Bài 3: Lũy thừa của một số hữu tỉ"},
                                {"id": "b4", "title": "Bài 4: Quy tắc dấu ngoặc và chuyển vế"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Số thực",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Số thập phân vô hạn tuần hoàn"},
                                {"id": "b6", "title": "Bài 6: Số vô tỉ, Căn bậc hai số học"},
                                {"id": "b7", "title": "Bài 7: Tập hợp các số thực"},
                                {"id": "b8", "title": "Bài 8: Giá trị tuyệt đối của một số thực"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Góc và hai đường thẳng song song",
                            "lessons": [
                                {"id": "b9", "title": "Bài 9: Hai góc kề bù, đối đỉnh"},
                                {"id": "b10", "title": "Bài 10: Tiên đề Euclid và dấu hiệu hai đường thẳng song song"},
                                {"id": "b11", "title": "Bài 11: Định lí và chứng minh định lí"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Tam giác bằng nhau",
                            "lessons": [
                                {"id": "b12", "title": "Bài 12: Tổng ba góc trong một tam giác"},
                                {"id": "b13", "title": "Bài 13: Hai tam giác bằng nhau (c-c-c, c-g-c, g-c-g)"},
                                {"id": "b14", "title": "Bài 14: Tam giác cân, đường trung trực"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chương 5: Thu thập và biểu diễn dữ liệu",
                            "lessons": [
                                {"id": "b15", "title": "Bài 15: Thu thập và phân loại dữ liệu"},
                                {"id": "b16", "title": "Bài 16: Biểu đồ hình quạt tròn và biểu đồ đoạn thẳng"},
                            ],
                        },
                        {
                            "id": "c6",
                            "title": "Chương 6: Tỉ lệ thức và đại lượng tỉ lệ",
                            "lessons": [
                                {"id": "b17", "title": "Bài 17: Tỉ lệ thức"},
                                {"id": "b18", "title": "Bài 18: Tính chất của dãy tỉ số bằng nhau"},
                                {"id": "b19", "title": "Bài 19: Đại lượng tỉ lệ thuận và tỉ lệ nghịch"},
                            ],
                        },
                        {
                            "id": "c7",
                            "title": "Chương 7: Biểu thức đại số và đa thức một biến",
                            "lessons": [
                                {"id": "b20", "title": "Bài 20: Đơn thức và đa thức một biến"},
                                {"id": "b21", "title": "Bài 21: Phép cộng và trừ đa thức một biến"},
                                {"id": "b22", "title": "Bài 22: Phép nhân và chia đa thức một biến"},
                            ],
                        },
                        {
                            "id": "c8",
                            "title": "Chương 8: Làm quen với biến cố và xác suất của biến cố",
                            "lessons": [
                                {"id": "b23", "title": "Bài 23: Biến cố ngẫu nhiên"},
                                {"id": "b24", "title": "Bài 24: Xác suất của biến cố"},
                            ],
                        },
                        {
                            "id": "c9",
                            "title": "Chương 9: Quan hệ giữa các yếu tố trong tam giác",
                            "lessons": [
                                {"id": "b25", "title": "Bài 25: Quan hệ giữa góc và cạnh đối diện trong tam giác"},
                                {"id": "b26", "title": "Bài 26: Bất đẳng thức tam giác"},
                                {
                                    "id": "b27",
                                    "title": "Bài 27: Các đường đồng quy trong tam giác (trung tuyến, phân giác, trung trực, cao)",
                                },
                            ],
                        },
                    ],
                },
                {
                    "slug": "ngu-van-7",
                    "name": "Ngữ văn 7",
                    "subject_code": "LIT",
                    "icon": "📖",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Bài 1: Bầu trời tuổi thơ (Truyện)",
                            "lessons": [
                                {"id": "b1", "title": "Văn bản 1: Bầy chim chìa vôi"},
                                {"id": "b2", "title": "Văn bản 2: Đi lấy mật (Đất rừng phương Nam)"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Bài 2: Khúc nhạc tâm hồn (Thơ bốn chữ, năm chữ)",
                            "lessons": [
                                {"id": "b3", "title": "Văn bản 1: Đồng dao mùa xuân"},
                                {"id": "b4", "title": "Văn bản 2: Gặp lá cơm nếp"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Bài 3: Cội nguồn yêu thương (Truyện)",
                            "lessons": [
                                {"id": "b5", "title": "Văn bản 1: Vừa nhắm mắt vừa mở cửa sổ"},
                                {"id": "b6", "title": "Văn bản 2: Người thầy đầu tiên"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Bài 4: Giai điệu đất nước (Thơ)",
                            "lessons": [
                                {"id": "b7", "title": "Văn bản 1: Mùa xuân nho nhỏ"},
                                {"id": "b8", "title": "Văn bản 2: Gò Me"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Bài 5: Màu sắc trăm miền (Tùy bút, tản văn)",
                            "lessons": [
                                {"id": "b9", "title": "Văn bản 1: Tháng Giêng, mơ về trăng non rét ngọt"},
                                {"id": "b10", "title": "Văn bản 2: Chuyện cơm hến"},
                            ],
                        },
                        {
                            "id": "c6",
                            "title": "Bài 6: Bài học cuộc sống (Truyện ngụ ngôn)",
                            "lessons": [
                                {"id": "b11", "title": "Văn bản 1: Đẽo cày giữa đường, Ếch ngồi đáy giếng"},
                                {"id": "b12", "title": "Văn bản 2: Thầy bói xem voi"},
                            ],
                        },
                        {
                            "id": "c7",
                            "title": "Bài 7: Thế giới viễn tưởng (Khoa học viễn tưởng)",
                            "lessons": [
                                {"id": "b13", "title": "Văn bản 1: Cuộc chạm trán trên đại dương"},
                                {"id": "b14", "title": "Văn bản 2: Đường vào trung tâm vũ trụ"},
                            ],
                        },
                        {
                            "id": "c8",
                            "title": "Bài 8: Trải nghiệm để trưởng thành (Nghị luận)",
                            "lessons": [
                                {"id": "b15", "title": "Văn bản 1: Bản đồ dẫn đường"},
                                {"id": "b16", "title": "Văn bản 2: Hãy cầm lấy và đọc"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tieng-anh-7",
                    "name": "Tiếng Anh 7",
                    "subject_code": "ENG",
                    "icon": "🇬🇧",
                    "chapters": [
                        {
                            "id": "u1",
                            "title": "Unit 1: Hobbies",
                            "lessons": [
                                {"id": "l1", "title": "Present Simple & Like + V-ing"},
                                {"id": "l2", "title": "Skills: Talking about favourite hobbies"},
                            ],
                        },
                        {
                            "id": "u2",
                            "title": "Unit 2: Healthy Living",
                            "lessons": [
                                {"id": "l3", "title": "Health problems & Tips"},
                                {"id": "l4", "title": "Simple sentences & Compound sentences"},
                            ],
                        },
                        {
                            "id": "u3",
                            "title": "Unit 3: Community Service",
                            "lessons": [
                                {"id": "l5", "title": "Volunteer activities"},
                                {"id": "l6", "title": "Past Simple tense"},
                            ],
                        },
                        {
                            "id": "u4",
                            "title": "Unit 4: Music and Arts",
                            "lessons": [
                                {"id": "l7", "title": "Artistic instruments"},
                                {"id": "l8", "title": "Comparisons: as...as, different from"},
                            ],
                        },
                        {
                            "id": "u5",
                            "title": "Unit 5: Food and Drink",
                            "lessons": [
                                {"id": "l9", "title": "Traditional food & Recipes"},
                                {"id": "l10", "title": "Countable & Uncountable nouns (some, any, much, many)"},
                            ],
                        },
                        {
                            "id": "u6",
                            "title": "Unit 6: A Visit to a School",
                            "lessons": [
                                {"id": "l11", "title": "School historical sites"},
                                {"id": "l12", "title": "Prepositions of time and place"},
                            ],
                        },
                        {
                            "id": "u7",
                            "title": "Unit 7: Traffic",
                            "lessons": [
                                {"id": "l13", "title": "Traffic rules & Road safety"},
                                {"id": "l14", "title": "It indicating distance & Used to"},
                            ],
                        },
                        {
                            "id": "u8",
                            "title": "Unit 8: Films",
                            "lessons": [
                                {"id": "l15", "title": "Film genres & Reviews"},
                                {"id": "l16", "title": "Connectors: Although, However"},
                            ],
                        },
                        {
                            "id": "u9",
                            "title": "Unit 9: Festivals around the World",
                            "lessons": [
                                {"id": "l17", "title": "Cultural festivals"},
                                {"id": "l18", "title": "Yes/No questions in Past and Present"},
                            ],
                        },
                        {
                            "id": "u10",
                            "title": "Unit 10: Energy Sources",
                            "lessons": [
                                {"id": "l19", "title": "Renewable & Non-renewable energy"},
                                {"id": "l20", "title": "Present Continuous for future"},
                            ],
                        },
                        {
                            "id": "u11",
                            "title": "Unit 11: Travelling in the Future",
                            "lessons": [
                                {"id": "l21", "title": "Future means of transport"},
                                {"id": "l22", "title": "Future Simple (will) & Possessive pronouns"},
                            ],
                        },
                        {
                            "id": "u12",
                            "title": "Unit 12: English Speaking Countries",
                            "lessons": [
                                {"id": "l23", "title": "Facts about English countries"},
                                {"id": "l24", "title": "Articles: a, an, the"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "khoa-hoc-tu-nhien-7",
                    "name": "Khoa học tự nhiên 7",
                    "subject_code": "SCI",
                    "icon": "🔬",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Nguyên tử - Nguyên tố hóa học - Sơ lược Bảng tuần hoàn",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Phương pháp học tập KHTN"},
                                {"id": "b2", "title": "Bài 2: Nguyên tử và cấu tạo nguyên tử"},
                                {"id": "b3", "title": "Bài 3: Nguyên tố hóa học"},
                                {"id": "b4", "title": "Bài 4: Sơ lược về bảng tuần hoàn các nguyên tố hóa học"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Phân tử - Liên kết hóa học",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Phân tử - Đơn chất - Hợp chất"},
                                {"id": "b6", "title": "Bài 6: Liên kết hóa học (liên kết ion, liên kết cộng hóa trị)"},
                                {"id": "b7", "title": "Bài 7: Hóa trị và công thức hóa học"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Tốc độ chuyển động",
                            "lessons": [
                                {"id": "b8", "title": "Bài 8: Tốc độ chuyển động (v = s/t)"},
                                {"id": "b9", "title": "Bài 9: Đo tốc độ"},
                                {"id": "b10", "title": "Bài 10: Đồ thị quãng đường - thời gian"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Âm thanh",
                            "lessons": [
                                {"id": "b11", "title": "Bài 11: Sóng âm, nguồn âm"},
                                {"id": "b12", "title": "Bài 12: Độ to và độ cao của âm"},
                                {"id": "b13", "title": "Bài 13: Phản xạ âm và chống ô nhiễm tiếng ồn"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chương 5: Ánh sáng",
                            "lessons": [
                                {"id": "b14", "title": "Bài 14: Sự phản xạ ánh sáng, định luật phản xạ ánh sáng"},
                                {"id": "b15", "title": "Bài 15: Năng lượng ánh sáng, tia sáng, vùng tối"},
                            ],
                        },
                        {
                            "id": "c6",
                            "title": "Chương 6: Từ trường",
                            "lessons": [
                                {"id": "b16", "title": "Bài 16: Từ trường và đường sức từ"},
                                {"id": "b17", "title": "Bài 17: Từ trường Trái Đất và la bàn"},
                                {"id": "b18", "title": "Bài 18: Nam châm điện"},
                            ],
                        },
                        {
                            "id": "c7",
                            "title": "Chương 7: Trao đổi chất và chuyển hóa năng lượng ở sinh vật",
                            "lessons": [
                                {"id": "b19", "title": "Bài 19: Khái quát về trao đổi chất và chuyển hóa năng lượng"},
                                {"id": "b20", "title": "Bài 20: Quang hợp ở thực vật"},
                                {"id": "b21", "title": "Bài 21: Hô hấp tế bào ở sinh vật"},
                                {"id": "b22", "title": "Bài 22: Trao đổi nước và chất dinh dưỡng ở sinh vật"},
                            ],
                        },
                        {
                            "id": "c8",
                            "title": "Chương 8: Cảm ứng, sinh trưởng và sinh sản ở sinh vật",
                            "lessons": [
                                {"id": "b23", "title": "Bài 23: Cảm ứng ở thực vật và động vật"},
                                {"id": "b24", "title": "Bài 24: Sinh trưởng và phát triển ở sinh vật"},
                                {"id": "b25", "title": "Bài 25: Sinh sản vô tính và sinh sản hữu tính"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "lich-su-va-dia-li-7",
                    "name": "Lịch sử và Địa lí 7",
                    "subject_code": "HIST_GEO",
                    "icon": "🗺️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Lịch sử: Tây Âu và Châu Á thời Trung đại",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Sự hình thành chế độ phong kiến Tây Âu"},
                                {"id": "b2", "title": "Bài 2: Các cuộc phát kiến địa lí"},
                                {"id": "b3", "title": "Bài 3: Phong trào Văn hóa Phục hưng"},
                                {"id": "b4", "title": "Bài 4: Trung Quốc và Ấn Độ thời phong kiến"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Lịch sử: Đại Việt qua các triều đại Đinh - Tiền Lê - Lý - Trần - Hồ - Lê Sơ",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Nhà nước Đinh - Tiền Lê"},
                                {"id": "b6", "title": "Bài 6: Nhà Lý và cuộc kháng chiến chống Tống"},
                                {
                                    "id": "b7",
                                    "title": "Bài 7: Ba lần kháng chiến chống quân xâm lược Mông - Nguyên thời Trần",
                                },
                                {"id": "b8", "title": "Bài 8: Khởi nghĩa Lam Sơn và nhà Lê Sơ"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Địa lí: Châu Âu và Châu Á",
                            "lessons": [
                                {"id": "b9", "title": "Bài 9: Vị trí địa lí, tự nhiên và dân cư Châu Âu"},
                                {"id": "b10", "title": "Bài 10: Liên minh Châu Âu (EU)"},
                                {"id": "b11", "title": "Bài 11: Thiên nhiên và con người Châu Á"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Địa lí: Châu Phi, Châu Mỹ, Châu Đại Dương, Châu Nam Cực",
                            "lessons": [
                                {"id": "b12", "title": "Bài 12: Châu Phi"},
                                {"id": "b13", "title": "Bài 13: Bắc Mỹ và Nam Mỹ"},
                                {"id": "b14", "title": "Bài 14: Châu Đại Dương và Châu Nam Cực"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tin-hoc-7",
                    "name": "Tin học 7",
                    "subject_code": "INFO",
                    "icon": "💻",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề: Bảng tính điện tử (Excel)",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Làm quen với bảng tính"},
                                {"id": "b2", "title": "Bài 2: Sử dụng công thức và hàm (SUM, AVERAGE, MAX, MIN)"},
                                {"id": "b3", "title": "Bài 3: Trình bày và in ấn bảng tính"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề: Thuật toán tìm kiếm và sắp xếp",
                            "lessons": [
                                {"id": "b4", "title": "Bài 4: Thuật toán tìm kiếm tuần tự và nhị phân"},
                                {"id": "b5", "title": "Bài 5: Thuật toán sắp xếp nổi bọt và chọn"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "cong-nghe-7",
                    "name": "Công nghệ 7",
                    "subject_code": "TECH",
                    "icon": "🛠️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Trồng trọt",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Nghề trồng trọt ở Việt Nam"},
                                {"id": "b2", "title": "Bài 2: Làm đất và bón phân lót"},
                                {"id": "b3", "title": "Bài 3: Gieo trồng và chăm sóc cây trồng"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Lâm nghiệp, Chăn nuôi và Thủy sản",
                            "lessons": [
                                {"id": "b4", "title": "Bài 4: Trồng và bảo vệ rừng"},
                                {"id": "b5", "title": "Bài 5: Kĩ thuật nuôi dưỡng và chăm sóc vật nuôi"},
                                {"id": "b6", "title": "Bài 6: Nuôi thủy sản nước ngọt"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "giao-duc-cong-dan-7",
                    "name": "Giáo dục công dân 7",
                    "subject_code": "CIVIC",
                    "icon": "⚖️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Đạo đức học sinh",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Tự hào về truyền thống quê hương"},
                                {"id": "b2", "title": "Bài 2: Quan tâm, cảm thông và chia sẻ"},
                                {"id": "b3", "title": "Bài 3: Học tập tự giác, tích cực"},
                                {"id": "b4", "title": "Bài 4: Giữ chữ tín"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Kĩ năng sống và Pháp luật",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Quản lí tiền"},
                                {"id": "b6", "title": "Bài 6: Phòng chống bạo lực học đường"},
                                {"id": "b7", "title": "Bài 7: Quyền trẻ em và bảo vệ trẻ em"},
                            ],
                        },
                    ],
                },
            ],
        }
    )

    # =========================================================================
    # LỚP 8
    # =========================================================================
    catalog.append(
        {
            "grade": 8,
            "subjects": [
                {
                    "slug": "toan-8",
                    "name": "Toán 8",
                    "subject_code": "MATH",
                    "icon": "📐",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Đa thức",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Đơn thức nhiều biến, đa thức nhiều biến"},
                                {"id": "b2", "title": "Bài 2: Các phép tính cộng, trừ, nhân đa thức"},
                                {"id": "b3", "title": "Bài 3: Phép chia đa thức cho đơn thức"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Hằng đẳng thức đáng nhớ và ứng dụng",
                            "lessons": [
                                {"id": "b4", "title": "Bài 4: Bảy hằng đẳng thức đáng nhớ"},
                                {"id": "b5", "title": "Bài 5: Phân tích đa thức thành nhân tử"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Tứ giác",
                            "lessons": [
                                {"id": "b6", "title": "Bài 6: Hình thang cân"},
                                {"id": "b7", "title": "Bài 7: Hình bình hành và hình chữ nhật"},
                                {"id": "b8", "title": "Bài 8: Hình thoi và hình vuông"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Định lí Thalès",
                            "lessons": [
                                {"id": "b9", "title": "Bài 9: Định lí Thalès trong tam giác"},
                                {"id": "b10", "title": "Bài 10: Đường trung bình của tam giác"},
                                {"id": "b11", "title": "Bài 11: Tính chất đường phân giác của tam giác"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chương 5: Dữ liệu và biểu đồ",
                            "lessons": [
                                {"id": "b12", "title": "Bài 12: Thu thập và tổ chức dữ liệu"},
                                {"id": "b13", "title": "Bài 13: Lựa chọn biểu đồ thích hợp"},
                            ],
                        },
                        {
                            "id": "c6",
                            "title": "Chương 6: Phân thức đại số",
                            "lessons": [
                                {"id": "b14", "title": "Bài 14: Khái niệm phân thức đại số, tính chất cơ bản"},
                                {"id": "b15", "title": "Bài 15: Phép cộng và trừ phân thức đại số"},
                                {"id": "b16", "title": "Bài 16: Phép nhân và chia phân thức đại số"},
                            ],
                        },
                        {
                            "id": "c7",
                            "title": "Chương 7: Phương trình bậc nhất và hàm số bậc nhất",
                            "lessons": [
                                {"id": "b17", "title": "Bài 17: Phương trình bậc nhất một ẩn (ax + b = 0)"},
                                {"id": "b18", "title": "Bài 18: Giải bài toán bằng cách lập phương trình"},
                                {"id": "b19", "title": "Bài 19: Khái niệm hàm số và đồ thị y = ax + b"},
                            ],
                        },
                        {
                            "id": "c8",
                            "title": "Chương 8: Tam giác đồng dạng và Hình khối trong thực tiễn",
                            "lessons": [
                                {"id": "b20", "title": "Bài 20: Hai tam giác đồng dạng (c-c-c, c-g-c, g-g)"},
                                {"id": "b21", "title": "Bài 21: Hình chóp tam giác đều và hình chóp tứ giác đều"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "ngu-van-8",
                    "name": "Ngữ văn 8",
                    "subject_code": "LIT",
                    "icon": "📖",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Bài 1: Vẻ đẹp cổ điển (Thơ Đường luật)",
                            "lessons": [
                                {"id": "b1", "title": "Văn bản 1: Thu điếu (Nguyễn Khuyến)"},
                                {"id": "b2", "title": "Văn bản 2: Qua Đèo Ngang (Bà Huyện Thanh Quan)"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Bài 2: Gương mặt thân yêu (Truyện ngắn)",
                            "lessons": [
                                {"id": "b3", "title": "Văn bản 1: Tôi đi học (Thanh Tịnh)"},
                                {"id": "b4", "title": "Văn bản 2: Gió lạnh đầu mùa"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Bài 3: Lời sông núi (Văn bản nghị luận trung đại)",
                            "lessons": [
                                {"id": "b5", "title": "Văn bản 1: Chiếu dời đô (Lý Công Uẩn)"},
                                {"id": "b6", "title": "Văn bản 2: Hịch tướng sĩ (Trần Quốc Tuấn)"},
                                {"id": "b7", "title": "Văn bản 3: Nước Đại Việt ta (Nguyễn Trãi)"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Bài 4: Tiếng cười trào phúng (Truyện cười)",
                            "lessons": [
                                {"id": "b8", "title": "Văn bản 1: Lợn cưới, áo mới"},
                                {"id": "b9", "title": "Văn bản 2: Treo biển"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tieng-anh-8",
                    "name": "Tiếng Anh 8",
                    "subject_code": "ENG",
                    "icon": "🇬🇧",
                    "chapters": [
                        {
                            "id": "u1",
                            "title": "Unit 1: Leisure Time",
                            "lessons": [
                                {"id": "l1", "title": "Verbs of liking/disliking + V-ing/To V"},
                                {"id": "l2", "title": "Skills & Project"},
                            ],
                        },
                        {
                            "id": "u2",
                            "title": "Unit 2: Life in the Countryside",
                            "lessons": [
                                {"id": "l3", "title": "Comparative adverbs"},
                                {"id": "l4", "title": "Life in the village"},
                            ],
                        },
                        {
                            "id": "u3",
                            "title": "Unit 3: Teenagers",
                            "lessons": [
                                {"id": "l5", "title": "Teen stress & Clubs"},
                                {"id": "l6", "title": "Simple and compound sentences"},
                            ],
                        },
                        {
                            "id": "u4",
                            "title": "Unit 4: Ethnic Groups of Viet Nam",
                            "lessons": [
                                {"id": "l7", "title": "Ethnic costumes & Festivals"},
                                {"id": "l8", "title": "Yes/No questions & Wh-questions"},
                            ],
                        },
                        {
                            "id": "u5",
                            "title": "Unit 5: Our Customs and Traditions",
                            "lessons": [
                                {"id": "l9", "title": "Customs vs Traditions"},
                                {"id": "l10", "title": "Zero article & Articles review"},
                            ],
                        },
                        {
                            "id": "u6",
                            "title": "Unit 6: Lifestyles",
                            "lessons": [
                                {"id": "l11", "title": "Nomadic vs Modern life"},
                                {"id": "l12", "title": "Future Simple & First Conditional"},
                            ],
                        },
                        {
                            "id": "u7",
                            "title": "Unit 7: Environmental Protection",
                            "lessons": [
                                {"id": "l13", "title": "Pollution & Habitat loss"},
                                {"id": "l14", "title": "Complex sentences with adverb clauses"},
                            ],
                        },
                        {
                            "id": "u8",
                            "title": "Unit 8: Shopping",
                            "lessons": [
                                {"id": "l15", "title": "Supermarkets vs Open markets"},
                                {"id": "l16", "title": "Present Simple for future schedules"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "khoa-hoc-tu-nhien-8",
                    "name": "Khoa học tự nhiên 8",
                    "subject_code": "SCI",
                    "icon": "🔬",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Phản ứng hóa học",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Biến đổi vật lí và biến đổi hóa học"},
                                {"id": "b2", "title": "Bài 2: Phản ứng hóa học và năng lượng của phản ứng"},
                                {"id": "b3", "title": "Bài 3: Định luật bảo toàn khối lượng và phương trình hóa học"},
                                {"id": "b4", "title": "Bài 4: Mol, tỉ khối của chất khí, nồng độ dung dịch"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Một số hợp chất thông dụng (Acid, Base, Oxide, Muối)",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Acid và độ pH"},
                                {"id": "b6", "title": "Bài 6: Base và thang pH"},
                                {"id": "b7", "title": "Bài 7: Oxide và Muối, Phân bón hóa học"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Khối lượng riêng và Áp suất",
                            "lessons": [
                                {"id": "b8", "title": "Bài 8: Khối lượng riêng (D = m/V)"},
                                {"id": "b9", "title": "Bài 9: Áp suất chất rắn, chất lỏng và khí quyển"},
                                {"id": "b10", "title": "Bài 10: Lực đẩy Archimedes"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Tác dụng làm quay của lực (Đòn bẩy, Momen lực)",
                            "lessons": [
                                {"id": "b11", "title": "Bài 11: Momen lực và tác dụng làm quay"},
                                {"id": "b12", "title": "Bài 12: Đòn bẩy và ứng dụng"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chương 5: Điện và Nhiệt",
                            "lessons": [
                                {
                                    "id": "b13",
                                    "title": "Bài 13: Sự nhiễm điện, mạch điện và các tác dụng của dòng điện",
                                },
                                {
                                    "id": "b14",
                                    "title": "Bài 14: Nhiệt năng, sự truyền nhiệt và dẫn nhiệt, đối lưu, bức xạ nhiệt",
                                },
                            ],
                        },
                        {
                            "id": "c6",
                            "title": "Chương 6: Sinh học cơ thể người",
                            "lessons": [
                                {"id": "b15", "title": "Bài 15: Hệ vận động"},
                                {"id": "b16", "title": "Bài 16: Hệ tuần hoàn và miễn dịch"},
                                {"id": "b17", "title": "Bài 17: Hệ hô hấp và tiêu hóa"},
                                {"id": "b18", "title": "Bài 18: Hệ bài tiết, thần kinh và nội tiết"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "lich-su-va-dia-li-8",
                    "name": "Lịch sử và Địa lí 8",
                    "subject_code": "HIST_GEO",
                    "icon": "🗺️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Lịch sử: Cách mạng tư sản và Chủ nghĩa tư bản thế giới",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Các cuộc cách mạng tư sản Anh, Bắc Mỹ, Pháp"},
                                {"id": "b2", "title": "Bài 2: Cách mạng công nghiệp thế kỉ 18 - 19"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Lịch sử: Việt Nam từ thế kỉ 16 đến thế kỉ 19",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Xung đột Nam - Bắc triều và Trịnh - Nguyễn"},
                                {"id": "b4", "title": "Bài 4: Phong trào Tây Sơn và vua Quang Trung"},
                                {
                                    "id": "b5",
                                    "title": "Bài 5: Nhà Nguyễn và cuộc kháng chiến chống Pháp cuối thế kỉ 19",
                                },
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Địa lí: Địa lí tự nhiên Việt Nam",
                            "lessons": [
                                {"id": "b6", "title": "Bài 6: Vị trí địa lí và phạm vi lãnh thổ VN"},
                                {"id": "b7", "title": "Bài 7: Địa hình và khoáng sản VN"},
                                {"id": "b8", "title": "Bài 8: Khí hậu và thủy văn VN"},
                                {"id": "b9", "title": "Bài 9: Thổ nhưỡng và sinh vật VN"},
                                {"id": "b10", "title": "Bài 10: Biển đảo Việt Nam"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tin-hoc-8",
                    "name": "Tin học 8",
                    "subject_code": "INFO",
                    "icon": "💻",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề: Lập trình với ngôn ngữ trực quan hoặc Python",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Lịch sử máy tính và xu hướng phát triển"},
                                {"id": "b2", "title": "Bài 2: Biến, kiểu dữ liệu và biểu thức"},
                                {"id": "b3", "title": "Bài 3: Cấu trúc rẽ nhánh (if - else)"},
                                {"id": "b4", "title": "Bài 4: Cấu trúc lặp (for, while)"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "cong-nghe-8",
                    "name": "Công nghệ 8",
                    "subject_code": "TECH",
                    "icon": "🛠️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Vẽ kĩ thuật",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Tiêu chuẩn bản vẽ kĩ thuật"},
                                {"id": "b2", "title": "Bài 2: Hình chiếu vuông góc và bản vẽ chi tiết"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Cơ khí và Kĩ thuật điện",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Vật liệu cơ khí và gia công cơ khí bằng tay"},
                                {"id": "b4", "title": "Bài 4: Truyền và biến đổi chuyển động"},
                                {"id": "b5", "title": "Bài 5: An toàn điện và mạch điện điều khiển đơn giản"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "giao-duc-cong-dan-8",
                    "name": "Giáo dục công dân 8",
                    "subject_code": "CIVIC",
                    "icon": "⚖️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Tự hào dân tộc và Tôn trọng pháp luật",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Tự hào về truyền thống dân tộc Việt Nam"},
                                {"id": "b2", "title": "Bài 2: Tôn trọng sự đa dạng văn hóa"},
                                {"id": "b3", "title": "Bài 3: Lao động cần cù, sáng tạo"},
                                {"id": "b4", "title": "Bài 4: Bảo vệ lẽ phải và bảo vệ môi trường"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Pháp luật và quyền công dân",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Phòng chống bạo lực gia đình"},
                                {"id": "b6", "title": "Bài 6: Quyền và nghĩa vụ của công dân về sở hữu tài sản"},
                            ],
                        },
                    ],
                },
            ],
        }
    )

    # =========================================================================
    # LỚP 9
    # =========================================================================
    catalog.append(
        {
            "grade": 9,
            "subjects": [
                {
                    "slug": "toan-9",
                    "name": "Toán 9",
                    "subject_code": "MATH",
                    "icon": "📐",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Phương trình và hệ hai phương trình bậc nhất hai ẩn",
                            "lessons": [
                                {
                                    "id": "b1",
                                    "title": "Bài 1: Khái niệm phương trình và hệ hai phương trình bậc nhất hai ẩn",
                                },
                                {"id": "b2", "title": "Bài 2: Phương pháp cộng đại số và phương pháp thế"},
                                {"id": "b3", "title": "Bài 3: Giải bài toán bằng cách lập hệ phương trình"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Phương trình bậc hai một ẩn và Hệ thức Viète",
                            "lessons": [
                                {"id": "b4", "title": "Bài 4: Phương trình bậc hai một ẩn ax² + bx + c = 0"},
                                {"id": "b5", "title": "Bài 5: Định lí Viète và ứng dụng nhẩm nghiệm"},
                                {"id": "b6", "title": "Bài 6: Giải bài toán bằng cách lập phương trình bậc hai"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Căn thức bậc hai và Căn thức bậc ba",
                            "lessons": [
                                {"id": "b7", "title": "Bài 7: Căn bậc hai và căn thức bậc hai"},
                                {
                                    "id": "b8",
                                    "title": "Bài 8: Khai căn và các phép biến đổi đơn giản biểu thức chứa căn",
                                },
                                {"id": "b9", "title": "Bài 9: Rút gọn biểu thức chứa căn bậc hai"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Hệ thức lượng trong tam giác vuông",
                            "lessons": [
                                {"id": "b10", "title": "Bài 10: Tỉ số lượng giác của góc nhọn (sin, cos, tan, cot)"},
                                {"id": "b11", "title": "Bài 11: Một số hệ thức về cạnh và góc trong tam giác vuông"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chương 5: Đường tròn",
                            "lessons": [
                                {"id": "b12", "title": "Bài 12: Sự xác định đường tròn và tính chất đối xứng"},
                                {
                                    "id": "b13",
                                    "title": "Bài 13: Tiếp tuyến của đường tròn và tính chất hai tiếp tuyến cắt nhau",
                                },
                                {
                                    "id": "b14",
                                    "title": "Bài 14: Góc ở tâm, góc nội tiếp, góc tạo bởi tia tiếp tuyến và dây cung",
                                },
                                {"id": "b15", "title": "Bài 15: Tứ giác nội tiếp đường tròn"},
                            ],
                        },
                        {
                            "id": "c6",
                            "title": "Chương 6: Một số hình khối trong thực tiễn & Thống kê",
                            "lessons": [
                                {"id": "b16", "title": "Bài 16: Hình trụ, hình nón, hình cầu (diện tích và thể tích)"},
                                {"id": "b17", "title": "Bài 17: Bảng tần số, biểu đồ tần số và xác suất biến cố"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "ngu-van-9",
                    "name": "Ngữ văn 9",
                    "subject_code": "LIT",
                    "icon": "📖",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Bài 1: Khát vọng hoà bình (Truyện truyền kì)",
                            "lessons": [
                                {"id": "b1", "title": "Văn bản 1: Chuyện người con gái Nam Xương (Nguyễn Dữ)"},
                                {"id": "b2", "title": "Văn bản 2: Dế Mèn phiêu lưu kí"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Bài 2: Vẻ đẹp thi ca cổ điển (Truyện Kiều)",
                            "lessons": [
                                {"id": "b3", "title": "Văn bản 1: Chị em Thúy Kiều (Nguyễn Du)"},
                                {"id": "b4", "title": "Văn bản 2: Cảnh ngày xuân"},
                                {"id": "b5", "title": "Văn bản 3: Kiều ở lầu Ngưng Bích"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Bài 3: Lòng yêu nước và tình đồng chí (Thơ hiện đại)",
                            "lessons": [
                                {"id": "b6", "title": "Văn bản 1: Đồng chí (Chính Hữu)"},
                                {"id": "b7", "title": "Văn bản 2: Bài thơ về tiểu đội xe không kính (Phạm Tiến Duật)"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Bài 4: Tình cảm gia đình (Truyện ngắn)",
                            "lessons": [
                                {"id": "b8", "title": "Văn bản 1: Chiếc lược ngà (Nguyễn Quang Sáng)"},
                                {"id": "b9", "title": "Văn bản 2: Bếp lửa (Bằng Việt)"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Bài 5: Khát vọng cống hiến",
                            "lessons": [
                                {"id": "b10", "title": "Văn bản 1: Lặng lẽ Sa Pa (Nguyễn Thành Long)"},
                                {"id": "b11", "title": "Văn bản 2: Mùa xuân nho nhỏ (Thanh Hải)"},
                                {"id": "b12", "title": "Văn bản 3: Viếng lăng Bác (Viễn Phương)"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tieng-anh-9",
                    "name": "Tiếng Anh 9",
                    "subject_code": "ENG",
                    "icon": "🇬🇧",
                    "chapters": [
                        {
                            "id": "u1",
                            "title": "Unit 1: Local Community",
                            "lessons": [
                                {"id": "l1", "title": "Question words before to-infinitive"},
                                {"id": "l2", "title": "Phrasal verbs in community"},
                            ],
                        },
                        {
                            "id": "u2",
                            "title": "Unit 2: City Life",
                            "lessons": [
                                {"id": "l3", "title": "Comparison of adverbs"},
                                {"id": "l4", "title": "Double comparatives (The more... the better)"},
                            ],
                        },
                        {
                            "id": "u3",
                            "title": "Unit 3: Healthy Living for Teens",
                            "lessons": [
                                {"id": "l5", "title": "Modal verbs in first conditional"},
                                {"id": "l6", "title": "Managing stress and anxiety"},
                            ],
                        },
                        {
                            "id": "u4",
                            "title": "Unit 4: Remembering the Past",
                            "lessons": [
                                {"id": "l7", "title": "Past Continuous with When / While"},
                                {"id": "l8", "title": "Wish for the present (S + wish + Past)"},
                            ],
                        },
                        {
                            "id": "u5",
                            "title": "Unit 5: Our Experiences",
                            "lessons": [
                                {"id": "l9", "title": "Present Perfect with already, yet, just"},
                                {"id": "l10", "title": "Travel experiences"},
                            ],
                        },
                        {
                            "id": "u6",
                            "title": "Unit 6: Vietnamese Lifestyle: Then and Now",
                            "lessons": [
                                {"id": "l11", "title": "Used to & Would"},
                                {"id": "l12", "title": "Tradition vs Modern changes"},
                            ],
                        },
                        {
                            "id": "u7",
                            "title": "Unit 7: Natural Wonders of the World",
                            "lessons": [
                                {"id": "l13", "title": "Passive voice: Review"},
                                {"id": "l14", "title": "Impersonal passive: It is said that..."},
                            ],
                        },
                        {
                            "id": "u8",
                            "title": "Unit 8: Tourism",
                            "lessons": [
                                {"id": "l15", "title": "Compound nouns"},
                                {"id": "l16", "title": "Relative clauses (who, which, that, where)"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "khoa-hoc-tu-nhien-9",
                    "name": "Khoa học tự nhiên 9",
                    "subject_code": "SCI",
                    "icon": "🔬",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Năng lượng cơ học (Động năng, Thế năng, Cơ năng)",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Công và công suất"},
                                {"id": "b2", "title": "Bài 2: Động năng và thế năng trọng trường"},
                                {"id": "b3", "title": "Bài 3: Định luật bảo toàn và chuyển hóa cơ năng"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Ánh sáng (Khúc xạ ánh sáng, Thấu kính)",
                            "lessons": [
                                {"id": "b4", "title": "Bài 4: Khúc xạ ánh sáng và phản xạ toàn phần"},
                                {"id": "b5", "title": "Bài 5: Thấu kính hội tụ và thấu kính phân kì"},
                                {"id": "b6", "title": "Bài 6: Mắt và các tật của mắt (Cận thị, Viễn thị)"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Điện và Từ (Điện từ học)",
                            "lessons": [
                                {"id": "b7", "title": "Bài 7: Từ trường của ống dây có dòng điện"},
                                {"id": "b8", "title": "Bài 8: Hiện tượng cảm ứng điện từ và dòng điện xoay chiều"},
                                {"id": "b9", "title": "Bài 9: Máy biến thế và truyền tải điện năng đi xa"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Kim loại và Phi kim",
                            "lessons": [
                                {
                                    "id": "b10",
                                    "title": "Bài 10: Tính chất hóa học của kim loại và dãy hoạt động hóa học",
                                },
                                {"id": "b11", "title": "Bài 11: Gang, thép và sự ăn mòn kim loại"},
                                {"id": "b12", "title": "Bài 12: Phi kim: Carbon, Silicon"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chương 5: Hợp chất hữu cơ (Hydrocarbon & Dẫn xuất)",
                            "lessons": [
                                {"id": "b13", "title": "Bài 13: Methane, Ethylene, Acetylene"},
                                {"id": "b14", "title": "Bài 14: Rượu ethylic (Ethanol) và Axit axetic (Acetic acid)"},
                                {"id": "b15", "title": "Bài 15: Chất béo, Glucose, Tinh bột và Protein, Polymer"},
                            ],
                        },
                        {
                            "id": "c6",
                            "title": "Chương 6: Di truyền học và Tiến hóa",
                            "lessons": [
                                {"id": "b16", "title": "Bài 16: Các thí nghiệm của Men-đen và quy luật di truyền"},
                                {"id": "b17", "title": "Bài 17: Nhiễm sắc thể (NST), Nguyên phân và Giảm phân"},
                                {"id": "b18", "title": "Bài 18: DNA, RNA và quá trình tổng hợp Protein"},
                                {"id": "b19", "title": "Bài 19: Đột biến gen và đột biến NST"},
                                {"id": "b20", "title": "Bài 20: Ứng dụng di truyền học và thuyết tiến hóa"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "lich-su-va-dia-li-9",
                    "name": "Lịch sử và Địa lí 9",
                    "subject_code": "HIST_GEO",
                    "icon": "🗺️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Lịch sử: Thế giới từ năm 1918 đến nay",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Chiến tranh thế giới thứ nhất và thứ hai"},
                                {"id": "b2", "title": "Bài 2: Trật tự thế giới hai cực I-an-ta và Chiến tranh lạnh"},
                                {"id": "b3", "title": "Bài 3: Xu thế toàn cầu hóa và cách mạng khoa học công nghệ"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Lịch sử: Việt Nam từ năm 1918 đến nay",
                            "lessons": [
                                {
                                    "id": "b4",
                                    "title": "Bài 4: Phong trào dân tộc dân chủ và Đảng Cộng sản VN ra đời (1930)",
                                },
                                {"id": "b5", "title": "Bài 5: Cách mạng tháng Tám 1945 thành công"},
                                {"id": "b6", "title": "Bài 6: Kháng chiến chống thực dân Pháp (1945 - 1954)"},
                                {"id": "b7", "title": "Bài 7: Kháng chiến chống Mĩ cứu nước (1954 - 1975)"},
                                {"id": "b8", "title": "Bài 8: Đất nước trên con đường Đổi mới từ năm 1986"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Địa lí: Địa lí dân cư và các ngành kinh tế Việt Nam",
                            "lessons": [
                                {"id": "b9", "title": "Bài 9: Dân tộc, dân số và nguồn lao động VN"},
                                {"id": "b10", "title": "Bài 10: Nông nghiệp, lâm nghiệp và thủy sản"},
                                {"id": "b11", "title": "Bài 11: Công nghiệp và dịch vụ VN"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Địa lí: Các vùng kinh tế trọng điểm Việt Nam",
                            "lessons": [
                                {"id": "b12", "title": "Bài 12: Vùng Trung du và miền núi Bắc Bộ"},
                                {"id": "b13", "title": "Bài 13: Vùng Đồng bằng sông Hồng"},
                                {"id": "b14", "title": "Bài 14: Vùng Bắc Trung Bộ và Duyên hải Nam Trung Bộ"},
                                {"id": "b15", "title": "Bài 15: Vùng Tây Nguyên và Đông Nam Bộ"},
                                {"id": "b16", "title": "Bài 16: Vùng Đồng bằng sông Cửu Long và Kinh tế biển đảo"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tin-hoc-9",
                    "name": "Tin học 9",
                    "subject_code": "INFO",
                    "icon": "💻",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề: Dịch vụ đám mây & Phần mềm ứng dụng",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Lưu trữ đám mây và chia sẻ trực tuyến"},
                                {"id": "b2", "title": "Bài 2: Tạo trang web đơn giản bằng HTML/CSS"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề: Giải quyết vấn đề với sự trợ giúp của máy tính",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Thuật toán và chương trình Python"},
                                {"id": "b4", "title": "Bài 4: Định hướng nghề nghiệp ngành công nghệ thông tin"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "cong-nghe-9",
                    "name": "Công nghệ 9",
                    "subject_code": "TECH",
                    "icon": "🛠️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Định hướng nghề nghiệp và Mô đun Lắp đặt mạng điện trong nhà",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Nghề nghiệp trong lĩnh vực kĩ thuật, công nghệ"},
                                {"id": "b2", "title": "Bài 2: Thiết bị đóng cắt và bảo vệ mạng điện"},
                                {"id": "b3", "title": "Bài 3: Lắp đặt mạch điện bảng điện và mạch đèn huỳnh quang/LED"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "giao-duc-cong-dan-9",
                    "name": "Giáo dục công dân 9",
                    "subject_code": "CIVIC",
                    "icon": "⚖️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Đạo đức công dân & Lí tưởng sống",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Chí công vô tư"},
                                {"id": "b2", "title": "Bài 2: Tự chủ và trách nhiệm"},
                                {"id": "b3", "title": "Bài 3: Lí tưởng sống của thanh niên Việt Nam"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Pháp luật và Nhà nước CHXHCN Việt Nam",
                            "lessons": [
                                {
                                    "id": "b4",
                                    "title": "Bài 4: Quyền và nghĩa vụ của công dân trong hôn nhân và gia đình",
                                },
                                {"id": "b5", "title": "Bài 5: Quyền lao động và nghĩa vụ lao động"},
                                {"id": "b6", "title": "Bài 6: Quyền tham gia quản lí nhà nước và xã hội"},
                            ],
                        },
                    ],
                },
            ],
        }
    )

    # =========================================================================
    # LỚP 10
    # =========================================================================
    catalog.append(
        {
            "grade": 10,
            "subjects": [
                {
                    "slug": "toan-10",
                    "name": "Toán 10",
                    "subject_code": "MATH",
                    "icon": "📐",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Mệnh đề và Tập hợp",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Mệnh đề toán học, mệnh đề phủ định, kéo theo"},
                                {"id": "b2", "title": "Bài 2: Tập hợp và các phép toán trên tập hợp (giao, hợp, hiệu)"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Bất phương trình và Hệ bất phương trình bậc nhất hai ẩn",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Bất phương trình bậc nhất hai ẩn và miền nghiệm"},
                                {
                                    "id": "b4",
                                    "title": "Bài 4: Hệ bất phương trình bậc nhất hai ẩn và bài toán quy hoạch tuyến tính",
                                },
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Hệ thức lượng trong tam giác",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Giá trị lượng giác của góc từ 0° đến 180°"},
                                {"id": "b6", "title": "Bài 6: Định lí côsin và định lí sin trong tam giác"},
                                {"id": "b7", "title": "Bài 7: Các công thức tính diện tích tam giác và giải tam giác"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Vectơ",
                            "lessons": [
                                {"id": "b8", "title": "Bài 8: Khái niệm vectơ, độ dài và hướng"},
                                {"id": "b9", "title": "Bài 9: Tổng và hiệu của hai vectơ"},
                                {"id": "b10", "title": "Bài 10: Tích của một số với một vectơ"},
                                {"id": "b11", "title": "Bài 11: Tích vô hướng của hai vectơ"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chương 5: Các số đặc trưng đo xu thế trung tâm và độ phân tán của mẫu số liệu",
                            "lessons": [
                                {"id": "b12", "title": "Bài 12: Số trung bình, trung vị, tứ phân vị, mốt"},
                                {
                                    "id": "b13",
                                    "title": "Bài 13: Khoảng biến thiên, khoảng tứ phân vị, phương sai và độ lệch chuẩn",
                                },
                            ],
                        },
                        {
                            "id": "c6",
                            "title": "Chương 6: Hàm số, Đồ thị và Ứng dụng",
                            "lessons": [
                                {"id": "b14", "title": "Bài 14: Hàm số và tập xác định, sự đồng biến, nghịch biến"},
                                {"id": "b15", "title": "Bài 15: Hàm số bậc hai y = ax² + bx + c và đồ thị parabol"},
                                {
                                    "id": "b16",
                                    "title": "Bài 16: Dấu của tam thức bậc hai và bất phương trình bậc hai một ẩn",
                                },
                            ],
                        },
                        {
                            "id": "c7",
                            "title": "Chương 7: Phương pháp tọa độ trong mặt phẳng (Oxy)",
                            "lessons": [
                                {"id": "b17", "title": "Bài 17: Tọa độ của vectơ và điểm"},
                                {"id": "b18", "title": "Bài 18: Phương trình đường thẳng (tham số, tổng quát)"},
                                {
                                    "id": "b19",
                                    "title": "Bài 19: Khoảng cách từ điểm đến đường thẳng, góc giữa hai đường thẳng",
                                },
                                {"id": "b20", "title": "Bài 20: Phương trình đường tròn"},
                                {"id": "b21", "title": "Bài 21: Ba đường conic (Ellipse, Hyperbol, Parabol)"},
                            ],
                        },
                        {
                            "id": "c8",
                            "title": "Chương 8: Đại số tổ hợp & Xác suất cổ điển",
                            "lessons": [
                                {"id": "b22", "title": "Bài 22: Quy tắc cộng, quy tắc nhân và sơ đồ cây"},
                                {"id": "b23", "title": "Bài 23: Hoán vị, Chỉnh hợp và Tổ hợp (Pn, An^k, Cn^k)"},
                                {"id": "b24", "title": "Bài 24: Nhị thức Newton"},
                                {"id": "b25", "title": "Bài 25: Xác suất của biến cố theo định nghĩa cổ điển"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "vat-li-10",
                    "name": "Vật lí 10",
                    "subject_code": "PHYSICS",
                    "icon": "⚡",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Mở đầu về Vật lí & Sai số thực nghiệm",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Khái quát về môn Vật lí"},
                                {"id": "b2", "title": "Bài 2: Vấn đề an toàn và sai số trong phép đo đại lượng vật lí"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Động học (Chuyển động thẳng đều, biến đổi đều, rơi tự do)",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Vận tốc, độ dịch chuyển"},
                                {
                                    "id": "b4",
                                    "title": "Bài 4: Chuyển động thẳng biến đổi đều (Gia tốc, vận tốc, quãng đường)",
                                },
                                {"id": "b5", "title": "Bài 5: Rơi tự do"},
                                {"id": "b6", "title": "Bài 6: Chuyển động ném ngang, ném xiên"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Động lực học (Các định luật Newton & Các lực cơ học)",
                            "lessons": [
                                {"id": "b7", "title": "Bài 7: Tổng hợp và phân tích lực"},
                                {"id": "b8", "title": "Bài 8: Ba định luật Newton (I, II, III)"},
                                {"id": "b9", "title": "Bài 9: Trọng lực, Lực căng dây, Lực ma sát, Lực cản và lực đẩy"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Năng lượng, Công và Công suất",
                            "lessons": [
                                {"id": "b10", "title": "Bài 10: Công cơ học và Công suất"},
                                {"id": "b11", "title": "Bài 11: Động năng, Thế năng và Cơ năng"},
                                {"id": "b12", "title": "Bài 12: Định luật bảo toàn cơ năng và Hiệu suất"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chương 5: Động lượng & Va chạm",
                            "lessons": [
                                {"id": "b13", "title": "Bài 13: Động lượng và Xung lượng của lực"},
                                {"id": "b14", "title": "Bài 14: Định luật bảo toàn động lượng và Va chạm đàn hồi, mềm"},
                            ],
                        },
                        {
                            "id": "c6",
                            "title": "Chương 6: Chuyển động tròn đều",
                            "lessons": [
                                {"id": "b15", "title": "Bài 15: Tốc độ góc, chu kì, tần số"},
                                {"id": "b16", "title": "Bài 16: Gia tốc hướng tâm và Lực hướng tâm"},
                            ],
                        },
                        {
                            "id": "c7",
                            "title": "Chương 7: Biến dạng của vật rắn & Áp suất chất lưu",
                            "lessons": [
                                {"id": "b17", "title": "Bài 17: Biến dạng đàn hồi và Định luật Hooke"},
                                {"id": "b18", "title": "Bài 18: Áp suất chất lỏng và khối lượng riêng"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "hoa-hoc-10",
                    "name": "Hóa học 10",
                    "subject_code": "CHEMISTRY",
                    "icon": "🧪",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Cấu tạo nguyên tử",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Thành phần nguyên tử (proton, neutron, electron)"},
                                {"id": "b2", "title": "Bài 2: Hạt nhân nguyên tử, nguyên tố hóa học, đồng vị"},
                                {"id": "b3", "title": "Bài 3: Cấu trúc lớp vỏ electron nguyên tử (AO, cấu hình e)"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Bảng tuần hoàn các nguyên tố hóa học",
                            "lessons": [
                                {"id": "b4", "title": "Bài 4: Cấu tạo bảng tuần hoàn (ô, chu kì, nhóm)"},
                                {
                                    "id": "b5",
                                    "title": "Bài 5: Xu hướng biến đổi bán kính, độ âm điện, tính kim loại phi kim",
                                },
                                {"id": "b6", "title": "Bài 6: Định luật tuần hoàn và ý nghĩa của bảng tuần hoàn"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Liên kết hóa học",
                            "lessons": [
                                {"id": "b7", "title": "Bài 7: Quy tắc Octet"},
                                {"id": "b8", "title": "Bài 8: Liên kết ion"},
                                {
                                    "id": "b9",
                                    "title": "Bài 9: Liên kết cộng hóa trị (phân cực, không phân cực, cho nhận)",
                                },
                                {"id": "b10", "title": "Bài 10: Liên kết hydrogen và tương tác van der Waals"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Phản ứng oxi hóa - khử",
                            "lessons": [
                                {"id": "b11", "title": "Bài 11: Số oxi hóa và quy tắc xác định"},
                                {
                                    "id": "b12",
                                    "title": "Bài 12: Phản ứng oxi hóa - khử và cân bằng theo phương pháp thăng bằng electron",
                                },
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chương 5: Năng lượng hóa học (Enthalpy)",
                            "lessons": [
                                {"id": "b13", "title": "Bài 13: Phản ứng tỏa nhiệt và thu nhiệt"},
                                {"id": "b14", "title": "Bài 14: Biến thiên enthalpy chuẩn của phản ứng (ΔrHo)"},
                            ],
                        },
                        {
                            "id": "c6",
                            "title": "Chương 6: Tốc độ phản ứng hóa học",
                            "lessons": [
                                {"id": "b15", "title": "Bài 15: Khái niệm tốc độ phản ứng"},
                                {
                                    "id": "b16",
                                    "title": "Bài 16: Các yếu tố ảnh hưởng đến tốc độ phản ứng (nồng độ, nhiệt độ, áp suất, diện tích tiếp xúc, chất xúc tác)",
                                },
                            ],
                        },
                        {
                            "id": "c7",
                            "title": "Chương 7: Nhóm nguyên tố Halogen (Nhóm VIIA)",
                            "lessons": [
                                {"id": "b17", "title": "Bài 17: Đơn chất Halogen (F2, Cl2, Br2, I2)"},
                                {"id": "b18", "title": "Bài 18: Hydrogen halide và một số phản ứng của ion halide"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "sinh-hoc-10",
                    "name": "Sinh học 10",
                    "subject_code": "BIOLOGY",
                    "icon": "🧬",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Mở đầu & Thành phần hóa học của tế bào",
                            "lessons": [
                                {
                                    "id": "b1",
                                    "title": "Bài 1: Giới thiệu khái quát môn Sinh học và các cấp độ tổ chức của thế giới sống",
                                },
                                {"id": "b2", "title": "Bài 2: Nước và các nguyên tố khoáng"},
                                {
                                    "id": "b3",
                                    "title": "Bài 3: Các phân tử sinh học (Carbohydrate, Lipid, Protein, Nucleic acid)",
                                },
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Cấu trúc tế bào",
                            "lessons": [
                                {"id": "b4", "title": "Bài 4: Tế bào nhân sơ"},
                                {"id": "b5", "title": "Bài 5: Tế bào nhân thực (màng sinh chất, nhân, bào quan)"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Trao đổi chất và chuyển hóa năng lượng ở tế bào",
                            "lessons": [
                                {"id": "b6", "title": "Bài 6: Vận chuyển các chất qua màng sinh chất"},
                                {"id": "b7", "title": "Bài 7: Enzyme và chuyển hóa vật chất"},
                                {"id": "b8", "title": "Bài 8: Hô hấp tế bào và Quang hợp"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Chu kì tế bào và phân bào",
                            "lessons": [
                                {"id": "b9", "title": "Bài 9: Chu kì tế bào và Nguyên phân"},
                                {"id": "b10", "title": "Bài 10: Giảm phân và thụ tinh"},
                                {"id": "b11", "title": "Bài 11: Công nghệ tế bào"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chương 5: Vi sinh vật và Virus",
                            "lessons": [
                                {"id": "b12", "title": "Bài 12: Khái quát về vi sinh vật và dinh dưỡng vi sinh vật"},
                                {"id": "b13", "title": "Bài 13: Sinh trưởng và sinh sản của vi sinh vật"},
                                {"id": "b14", "title": "Bài 14: Cấu tạo virus và quá trình nhân lên của virus"},
                                {"id": "b15", "title": "Bài 15: Ứng dụng của vi sinh vật và virus trong đời sống"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "ngu-van-10",
                    "name": "Ngữ văn 10",
                    "subject_code": "LIT",
                    "icon": "📖",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Bài 1: Sức sống của sử thi (Thần thoại và Sử thi)",
                            "lessons": [
                                {"id": "b1", "title": "Văn bản 1: Héc-to từ biệt Ăng-đrô-mác (I-li-át)"},
                                {"id": "b2", "title": "Văn bản 2: Đăm Săn đi bắt Nữ Thần Mặt Trời"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Bài 2: Vẻ đẹp của thơ trữ tình",
                            "lessons": [
                                {"id": "b3", "title": "Văn bản 1: Cảm xúc mùa thu (Đỗ Phủ)"},
                                {"id": "b4", "title": "Văn bản 2: Tự tình (Hồ Xuân Hương)"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Bài 3: Nghệ thuật chèo và tuồng",
                            "lessons": [
                                {"id": "b5", "title": "Văn bản 1: Xúy Vân giả dại (Chèo Kim Nham)"},
                                {"id": "b6", "title": "Văn bản 2: Huyện đường (Tuồng Nghêu, Sò, Ốc, Hến)"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Bài 4: Sức thuyết phục của văn bản nghị luận",
                            "lessons": [
                                {"id": "b7", "title": "Văn bản 1: Bản hòa âm ngôn từ trong bài Tiếng thu"},
                                {"id": "b8", "title": "Văn bản 2: Hiền tài là nguyên khí của quốc gia"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tieng-anh-10",
                    "name": "Tiếng Anh 10",
                    "subject_code": "ENG",
                    "icon": "🇬🇧",
                    "chapters": [
                        {
                            "id": "u1",
                            "title": "Unit 1: Family Life",
                            "lessons": [
                                {"id": "l1", "title": "Present Simple vs Present Continuous"},
                                {"id": "l2", "title": "Family routines & Chores"},
                            ],
                        },
                        {
                            "id": "u2",
                            "title": "Unit 2: Humans and the Environment",
                            "lessons": [
                                {"id": "l3", "title": "Passive Voice: Review"},
                                {"id": "l4", "title": "Eco-friendly lifestyle"},
                            ],
                        },
                        {
                            "id": "u3",
                            "title": "Unit 3: Music",
                            "lessons": [
                                {"id": "l5", "title": "Compound sentences & To-infinitive / Bare infinitive"},
                                {"id": "l6", "title": "Music shows and artists"},
                            ],
                        },
                        {
                            "id": "u4",
                            "title": "Unit 4: For a Better Community",
                            "lessons": [
                                {"id": "l7", "title": "Past Simple vs Past Continuous"},
                                {"id": "l8", "title": "Volunteer and non-profit projects"},
                            ],
                        },
                        {
                            "id": "u5",
                            "title": "Unit 5: Inventions",
                            "lessons": [
                                {"id": "l9", "title": "Present Perfect tense"},
                                {"id": "l10", "title": "Modern electronic inventions"},
                            ],
                        },
                        {
                            "id": "u6",
                            "title": "Unit 6: Gender Equality",
                            "lessons": [
                                {"id": "l11", "title": "Passive voice with modal verbs"},
                                {"id": "l12", "title": "Equal opportunities in career"},
                            ],
                        },
                        {
                            "id": "u7",
                            "title": "Unit 7: Viet Nam and International Organisations",
                            "lessons": [
                                {"id": "l13", "title": "Comparative and superlative adjectives"},
                                {"id": "l14", "title": "UN, UNICEF, WTO"},
                            ],
                        },
                        {
                            "id": "u8",
                            "title": "Unit 8: New Ways to Learn",
                            "lessons": [
                                {"id": "l15", "title": "Relative pronouns (who, which, that)"},
                                {"id": "l16", "title": "Online and blended learning"},
                            ],
                        },
                        {
                            "id": "u9",
                            "title": "Unit 9: Protecting the Environment",
                            "lessons": [
                                {"id": "l17", "title": "Reported speech: Statements"},
                                {"id": "l18", "title": "Endangered species and biodiversity"},
                            ],
                        },
                        {
                            "id": "u10",
                            "title": "Unit 10: Ecotourism",
                            "lessons": [
                                {"id": "l19", "title": "Conditional sentences Type 1 and Type 2"},
                                {"id": "l20", "title": "Sustainable and ethical tourism"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "lich-su-10",
                    "name": "Lịch sử 10",
                    "subject_code": "HIST",
                    "icon": "🏛️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề 1: Lịch sử và Sử học",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Khái niệm Sử học và chức năng của Lịch sử"},
                                {"id": "b2", "title": "Bài 2: Tri thức lịch sử và cuộc sống"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề 2: Các nền văn minh cổ đại thế giới",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Văn minh Ai Cập, Lưỡng Hà, Ấn Độ, Trung Hoa"},
                                {"id": "b4", "title": "Bài 4: Văn minh Hy Lạp, La Mã và thời Phục hưng"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chủ đề 3: Văn minh Đông Nam Á và Văn minh Đại Việt",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Khái quát văn minh Đông Nam Á thời cổ - trung đại"},
                                {"id": "b6", "title": "Bài 6: Văn minh Văn Lang - Âu Lạc, Chăm-pa, Phù Nam"},
                                {"id": "b7", "title": "Bài 7: Văn minh Đại Việt và thành tựu rực rỡ"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "dia-li-10",
                    "name": "Địa lí 10",
                    "subject_code": "GEO",
                    "icon": "🌍",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Sử dụng bản đồ & Thạch quyển",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Phương pháp biểu hiện các đối tượng trên bản đồ"},
                                {
                                    "id": "b2",
                                    "title": "Bài 2: Thuyết kiến tạo mảng, nội lực và ngoại lực tác động đến địa hình",
                                },
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Khí quyển, Thủy quyển và Sinh quyển",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Khí quyển, sự phân bố nhiệt độ và khí áp, gió"},
                                {"id": "b4", "title": "Bài 4: Thủy quyển, sóng, thủy triều, dòng biển"},
                                {"id": "b5", "title": "Bài 5: Thổ nhưỡng và sinh quyển, quy luật địa đới"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Địa lí dân cư và Địa lí các ngành kinh tế",
                            "lessons": [
                                {"id": "b6", "title": "Bài 6: Dân số và cơ cấu dân số thế giới"},
                                {"id": "b7", "title": "Bài 7: Địa lí nông nghiệp, công nghiệp và dịch vụ"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "giao-duc-kinh-te-va-phap-luat-10",
                    "name": "Giáo dục kinh tế và pháp luật 10",
                    "subject_code": "CIVIC",
                    "icon": "⚖️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề 1: Nền kinh tế và các chủ thể kinh tế",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Các hoạt động kinh tế cơ bản trong đời sống xã hội"},
                                {"id": "b2", "title": "Bài 2: Các chủ thể của nền kinh tế"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề 2: Thị trường, Cơ chế thị trường, Ngân sách và Thuế",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Thị trường và cơ chế thị trường"},
                                {"id": "b4", "title": "Bài 4: Ngân sách nhà nước và thuế"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chủ đề 3: Hệ thống chính trị và Pháp luật nước CHXHCN Việt Nam",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Hệ thống chính trị nước CHXHCN Việt Nam"},
                                {"id": "b6", "title": "Bài 6: Hiến pháp và hệ thống văn bản quy phạm pháp luật"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tin-hoc-10",
                    "name": "Tin học 10",
                    "subject_code": "INFO",
                    "icon": "💻",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề 1: Máy tính và xã hội tri thức",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Thông tin và xử lí thông tin trong kĩ thuật số"},
                                {"id": "b2", "title": "Bài 2: Sự ưu việt của máy tính và điện toán đám mây"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề 2: Lập trình cơ bản với ngôn ngữ Python",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Làm quen với Python, biến và các kiểu dữ liệu"},
                                {"id": "b4", "title": "Bài 4: Cấu trúc rẽ nhánh if và câu lệnh lặp for, while"},
                                {"id": "b5", "title": "Bài 5: Xử lí xâu kí tự và danh sách (List)"},
                                {"id": "b6", "title": "Bài 6: Hàm tự định nghĩa trong Python"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "cong-nghe-10",
                    "name": "Công nghệ 10",
                    "subject_code": "TECH",
                    "icon": "🛠️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Khái quát về công nghệ và Thiết kế kĩ thuật",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Công nghệ và đời sống con người"},
                                {"id": "b2", "title": "Bài 2: Hệ thống kĩ thuật và quy trình thiết kế kĩ thuật"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Vẽ kĩ thuật và Ứng dụng CAD",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Tiêu chuẩn bản vẽ và các phép chiếu"},
                                {"id": "b4", "title": "Bài 4: Làm quen với phần mềm vẽ thiết kế 2D/3D"},
                            ],
                        },
                    ],
                },
            ],
        }
    )

    # =========================================================================
    # LỚP 11
    # =========================================================================
    catalog.append(
        {
            "grade": 11,
            "subjects": [
                {
                    "slug": "toan-11",
                    "name": "Toán 11",
                    "subject_code": "MATH",
                    "icon": "📐",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Hàm số lượng giác và phương trình lượng giác",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Giá trị lượng giác của góc lượng giác"},
                                {"id": "b2", "title": "Bài 2: Công thức lượng giác (cộng, nhân đôi, biến đổi)"},
                                {"id": "b3", "title": "Bài 3: Hàm số lượng giác (y = sinx, cosx, tanx, cotx)"},
                                {"id": "b4", "title": "Bài 4: Phương trình lượng giác cơ bản"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Dãy số. Cấp số cộng và cấp số nhân",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Dãy số và cách cho dãy số"},
                                {
                                    "id": "b6",
                                    "title": "Bài 6: Cấp số cộng (công sai, số hạng tổng quát, tổng n số hạng)",
                                },
                                {
                                    "id": "b7",
                                    "title": "Bài 7: Cấp số nhân (công bội, số hạng tổng quát, tổng n số hạng)",
                                },
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Các số đặc trưng đo xu thế trung tâm của mẫu số liệu ghép nhóm",
                            "lessons": [
                                {"id": "b8", "title": "Bài 8: Mẫu số liệu ghép nhóm"},
                                {
                                    "id": "b9",
                                    "title": "Bài 9: Các số đặc trưng: Số trung bình, trung vị, tứ phân vị, mốt của mẫu ghép nhóm",
                                },
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Quan hệ song song trong không gian",
                            "lessons": [
                                {"id": "b10", "title": "Bài 10: Điểm, đường thẳng và mặt phẳng trong không gian"},
                                {"id": "b11", "title": "Bài 11: Hai đường thẳng song song"},
                                {"id": "b12", "title": "Bài 12: Đường thẳng song song với mặt phẳng"},
                                {"id": "b13", "title": "Bài 13: Hai mặt phẳng song song và phép chiếu song song"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chương 5: Giới hạn. Hàm số liên tục",
                            "lessons": [
                                {"id": "b14", "title": "Bài 14: Giới hạn của dãy số"},
                                {"id": "b15", "title": "Bài 15: Giới hạn của hàm số (tại một điểm, vô cực, một bên)"},
                                {"id": "b16", "title": "Bài 16: Hàm số liên tục và định lí giá trị trung gian"},
                            ],
                        },
                        {
                            "id": "c6",
                            "title": "Chương 6: Hàm số mũ và hàm số lôgarit",
                            "lessons": [
                                {"id": "b17", "title": "Bài 17: Phép tính lũy thừa với số mũ thực"},
                                {"id": "b18", "title": "Bài 18: Lôgarit và các tính chất cơ bản"},
                                {"id": "b19", "title": "Bài 19: Hàm số mũ và hàm số lôgarit"},
                                {"id": "b20", "title": "Bài 20: Phương trình, bất phương trình mũ và lôgarit"},
                            ],
                        },
                        {
                            "id": "c7",
                            "title": "Chương 7: Đạo hàm",
                            "lessons": [
                                {
                                    "id": "b21",
                                    "title": "Bài 21: Định nghĩa và ý nghĩa hình học của đạo hàm (tiếp tuyến)",
                                },
                                {"id": "b22", "title": "Bài 22: Các quy tắc tính đạo hàm của hàm sơ cấp"},
                                {"id": "b23", "title": "Bài 23: Đạo hàm cấp hai và ứng dụng trong gia tốc"},
                            ],
                        },
                        {
                            "id": "c8",
                            "title": "Chương 8: Quan hệ vuông góc trong không gian",
                            "lessons": [
                                {"id": "b24", "title": "Bài 24: Hai đường thẳng vuông góc"},
                                {"id": "b25", "title": "Bài 25: Đường thẳng vuông góc với mặt phẳng"},
                                {"id": "b26", "title": "Bài 26: Hai mặt phẳng vuông góc, góc giữa hai mặt phẳng"},
                                {
                                    "id": "b27",
                                    "title": "Bài 27: Khoảng cách trong không gian (từ điểm đến mp, giữa 2 đường chéo nhau)",
                                },
                                {
                                    "id": "b28",
                                    "title": "Bài 28: Thể tích một số khối đa diện (lăng trụ, chóp, chóp cụt)",
                                },
                            ],
                        },
                        {
                            "id": "c9",
                            "title": "Chương 9: Các quy tắc tính xác suất",
                            "lessons": [
                                {"id": "b29", "title": "Bài 29: Biến cố giao và quy tắc nhân xác suất"},
                                {"id": "b30", "title": "Bài 30: Biến cố hợp và quy tắc cộng xác suất"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "vat-li-11",
                    "name": "Vật lí 11",
                    "subject_code": "PHYSICS",
                    "icon": "⚡",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Dao động",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Dao động điều hòa"},
                                {"id": "b2", "title": "Bài 2: Mô tả dao động điều hòa"},
                                {"id": "b3", "title": "Bài 3: Vận tốc, gia tốc trong dao động điều hòa"},
                                {
                                    "id": "b4",
                                    "title": "Bài 4: Năng lượng trong dao động điều hòa (Động năng, thế năng, cơ năng)",
                                },
                                {
                                    "id": "b5",
                                    "title": "Bài 5: Dao động tắt dần, dao động cưỡng bức và hiện tượng cộng hưởng",
                                },
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Sóng cơ & Sóng điện từ",
                            "lessons": [
                                {"id": "b6", "title": "Bài 6: Mô tả sóng và phương trình sóng"},
                                {"id": "b7", "title": "Bài 7: Sóng điện từ và thang sóng điện từ"},
                                {"id": "b8", "title": "Bài 8: Hiện tượng giao thoa sóng"},
                                {"id": "b9", "title": "Bài 9: Sóng dừng và điều kiện có sóng dừng"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Điện trường",
                            "lessons": [
                                {"id": "b10", "title": "Bài 10: Lực tương tác tĩnh điện (Định luật Coulomb)"},
                                {"id": "b11", "title": "Bài 11: Khái niệm điện trường và cường độ điện trường"},
                                {"id": "b12", "title": "Bài 12: Điện thế và thế năng tĩnh điện"},
                                {"id": "b13", "title": "Bài 13: Tụ điện, điện dung và năng lượng điện trường"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Dòng điện không đổi & Mạch điện",
                            "lessons": [
                                {"id": "b14", "title": "Bài 14: Cường độ dòng điện và điện lượng"},
                                {"id": "b15", "title": "Bài 15: Điện trở và định luật Ohm"},
                                {"id": "b16", "title": "Bài 16: Nguồn điện, suất điện động và năng lượng tiêu thụ"},
                                {"id": "b17", "title": "Bài 17: Đo suất điện động và điện trở trong của nguồn điện"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "hoa-hoc-11",
                    "name": "Hóa học 11",
                    "subject_code": "CHEMISTRY",
                    "icon": "🧪",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Cân bằng hóa học",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Khái niệm cân bằng hóa học và hằng số Kc"},
                                {
                                    "id": "b2",
                                    "title": "Bài 2: Cân bằng trong dung dịch nước, pH và thuyết Brønsted - Lowry",
                                },
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Nitrogen và Sulfur",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Đơn chất Nitrogen"},
                                {"id": "b4", "title": "Bài 4: Ammonia (NH3) và một số hợp chất ammonium"},
                                {"id": "b5", "title": "Bài 5: Một số hợp chất với oxygen của nitrogen (NO, NO2, HNO3)"},
                                {"id": "b6", "title": "Bài 6: Lưu huỳnh (Sulfur) và Sulfur dioxide (SO2)"},
                                {"id": "b7", "title": "Bài 7: Sulfuric acid (H2SO4) và muối sulfate"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Đại cương về hóa học hữu cơ",
                            "lessons": [
                                {"id": "b8", "title": "Bài 8: Hợp chất hữu cơ và hóa học hữu cơ"},
                                {"id": "b9", "title": "Bài 9: Phương pháp tách và tinh chế hợp chất hữu cơ"},
                                {"id": "b10", "title": "Bài 10: Công thức phân tử hợp chất hữu cơ"},
                                {"id": "b11", "title": "Bài 11: Cấu tạo hóa học và đồng phân, đồng đẳng"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Hydrocarbon",
                            "lessons": [
                                {"id": "b12", "title": "Bài 12: Alkane"},
                                {"id": "b13", "title": "Bài 13: Alkene và Alkyne"},
                                {"id": "b14", "title": "Bài 14: Arene (Hydrocarbon thơm, Benzene)"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chương 5: Dẫn xuất halogen - Alcohol - Phenol",
                            "lessons": [
                                {"id": "b15", "title": "Bài 15: Dẫn xuất halogen"},
                                {"id": "b16", "title": "Bài 16: Alcohol (Rượu, Ethanol, Glycerol)"},
                                {"id": "b17", "title": "Bài 17: Phenol"},
                            ],
                        },
                        {
                            "id": "c6",
                            "title": "Chương 6: Hợp chất carbonyl (Aldehyde - Ketone) và Carboxylic acid",
                            "lessons": [
                                {"id": "b18", "title": "Bài 18: Hợp chất carbonyl: Aldehyde và Ketone"},
                                {"id": "b19", "title": "Bài 19: Carboxylic acid (Axit hữu cơ)"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "sinh-hoc-11",
                    "name": "Sinh học 11",
                    "subject_code": "BIOLOGY",
                    "icon": "🧬",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Trao đổi chất và chuyển hóa năng lượng ở thực vật",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Khái quát về trao đổi chất và chuyển hóa năng lượng"},
                                {"id": "b2", "title": "Bài 2: Trao đổi nước và khoáng ở thực vật"},
                                {"id": "b3", "title": "Bài 3: Quang hợp ở thực vật (C3, C4, CAM)"},
                                {"id": "b4", "title": "Bài 4: Hô hấp ở thực vật"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Trao đổi chất và chuyển hóa năng lượng ở động vật",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Dinh dưỡng và tiêu hóa ở động vật"},
                                {"id": "b6", "title": "Bài 6: Hô hấp ở động vật"},
                                {"id": "b7", "title": "Bài 7: Hệ tuần hoàn ở động vật"},
                                {"id": "b8", "title": "Bài 8: Bài tiết và cân bằng nội môi"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Cảm ứng ở sinh vật",
                            "lessons": [
                                {"id": "b9", "title": "Bài 9: Cảm ứng ở thực vật (hướng động, ứng động)"},
                                {
                                    "id": "b10",
                                    "title": "Bài 10: Cảm ứng ở động vật (hệ thần kinh, điện thế nghỉ, điện thế hoạt động, synapse)",
                                },
                                {"id": "b11", "title": "Bài 11: Tập tính ở động vật"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Sinh trưởng và phát triển ở sinh vật",
                            "lessons": [
                                {
                                    "id": "b12",
                                    "title": "Bài 12: Sinh trưởng và phát triển ở thực vật (hormone thực vật)",
                                },
                                {
                                    "id": "b13",
                                    "title": "Bài 13: Sinh trưởng và phát triển ở động vật (biến thái và không biến thái)",
                                },
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chương 5: Sinh sản ở sinh vật",
                            "lessons": [
                                {"id": "b14", "title": "Bài 14: Sinh sản ở thực vật"},
                                {"id": "b15", "title": "Bài 15: Sinh sản ở động vật"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "ngu-van-11",
                    "name": "Ngữ văn 11",
                    "subject_code": "LIT",
                    "icon": "📖",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Bài 1: Câu chuyện và điểm nhìn trong truyện ngắn hiện đại",
                            "lessons": [
                                {"id": "b1", "title": "Văn bản 1: Vợ nhặt (Kim Lân)"},
                                {"id": "b2", "title": "Văn bản 2: Chí Phèo (Nam Cao)"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Bài 2: Cấu tứ và hình ảnh trong thơ trữ tình",
                            "lessons": [
                                {"id": "b3", "title": "Văn bản 1: Nhớ đồng (Tố Hữu)"},
                                {"id": "b4", "title": "Văn bản 2: Tràng giang (Huy Cận)"},
                                {"id": "b5", "title": "Văn bản 3: Đây thôn Vĩ Dạ (Hàn Mặc Tử)"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Bài 3: Tùy bút và tản văn",
                            "lessons": [
                                {"id": "b6", "title": "Văn bản 1: Người lái đò Sông Đà (Nguyễn Tuân)"},
                                {"id": "b7", "title": "Văn bản 2: Ai đã đặt tên cho dòng sông? (Hoàng Phủ Ngọc Tường)"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Bài 4: Bi kịch và hài kịch trong sân khấu",
                            "lessons": [
                                {"id": "b8", "title": "Văn bản 1: Vĩnh biệt Cửu Trùng Đài (Vũ Như Tô)"},
                                {"id": "b9", "title": "Văn bản 2: Sống hay không sống (Hamlet)"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tieng-anh-11",
                    "name": "Tiếng Anh 11",
                    "subject_code": "ENG",
                    "icon": "🇬🇧",
                    "chapters": [
                        {
                            "id": "u1",
                            "title": "Unit 1: A Long and Healthy Life",
                            "lessons": [
                                {"id": "l1", "title": "Past Simple vs Present Perfect"},
                                {"id": "l2", "title": "Diet and exercise for longevity"},
                            ],
                        },
                        {
                            "id": "u2",
                            "title": "Unit 2: The Generation Gap",
                            "lessons": [
                                {"id": "l3", "title": "Modal verbs: must, have to, should, ought to"},
                                {"id": "l4", "title": "Bridging family generation gaps"},
                            ],
                        },
                        {
                            "id": "u3",
                            "title": "Unit 3: Cities of the Future",
                            "lessons": [
                                {"id": "l5", "title": "Stative verbs in continuous forms"},
                                {"id": "l6", "title": "Smart cities and green infrastructure"},
                            ],
                        },
                        {
                            "id": "u4",
                            "title": "Unit 4: ASEAN and Viet Nam",
                            "lessons": [
                                {"id": "l7", "title": "Gerunds as subject and object"},
                                {"id": "l8", "title": "ASEAN youth and cooperation"},
                            ],
                        },
                        {
                            "id": "u5",
                            "title": "Unit 5: Global Warming",
                            "lessons": [
                                {"id": "l9", "title": "Present participle & Past participle clauses"},
                                {"id": "l10", "title": "Causes and impact of greenhouse effect"},
                            ],
                        },
                        {
                            "id": "u6",
                            "title": "Unit 6: Preserving Our Heritage",
                            "lessons": [
                                {"id": "l11", "title": "To-infinitive clauses"},
                                {"id": "l12", "title": "Tangible and intangible heritage of VN"},
                            ],
                        },
                        {
                            "id": "u7",
                            "title": "Unit 7: Education Options for School-Leavers",
                            "lessons": [
                                {"id": "l13", "title": "Perfect gerunds & Perfect participle clauses"},
                                {"id": "l14", "title": "Vocational vs Academic higher education"},
                            ],
                        },
                        {
                            "id": "u8",
                            "title": "Unit 8: Becoming Independent",
                            "lessons": [
                                {"id": "l15", "title": "Cleft sentences with 'It is/was... that'"},
                                {"id": "l16", "title": "Life skills and financial management"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "lich-su-11",
                    "name": "Lịch sử 11",
                    "subject_code": "HIST",
                    "icon": "🏛️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề 1: Cách mạng tư sản và Chủ nghĩa tư bản hiện đại",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Một số vấn đề chung về cách mạng tư sản"},
                                {"id": "b2", "title": "Bài 2: Sự xác lập và phát triển của chủ nghĩa tư bản độc quyền"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề 2: Chủ nghĩa xã hội và Phong trào giải phóng dân tộc",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Sự hình thành Liên bang Xô viết và hệ thống XHCN"},
                                {"id": "b4", "title": "Bài 4: Chiến tranh bảo vệ Tổ quốc trong lịch sử Việt Nam"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chủ đề 3: Lịch sử Biển Đông và Cuộc cải cách trong lịch sử VN",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Biển Đông: Vị trí chiến lược và chủ quyền của Việt Nam"},
                                {
                                    "id": "b6",
                                    "title": "Bài 6: Một số cuộc cải cách lớn trong lịch sử Việt Nam (Hồ Quý Ly, Lê Thánh Tông, Minh Mạng)",
                                },
                            ],
                        },
                    ],
                },
                {
                    "slug": "dia-li-11",
                    "name": "Địa lí 11",
                    "subject_code": "GEO",
                    "icon": "🌍",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Phần 1: Một số vấn đề kinh tế - xã hội thế giới",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Sự khác biệt về trình độ phát triển giữa các nhóm nước"},
                                {"id": "b2", "title": "Bài 2: Toàn cầu hóa và khu vực hóa kinh tế"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Phần 2: Địa lí khu vực và quốc gia",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Khu vực Mỹ La-tinh"},
                                {"id": "b4", "title": "Bài 4: Liên minh Châu Âu (EU)"},
                                {"id": "b5", "title": "Bài 5: Khu vực Đông Nam Á (ASEAN)"},
                                {"id": "b6", "title": "Bài 6: Hợp chủng quốc Hoa Kỳ (Hoa Kỳ)"},
                                {"id": "b7", "title": "Bài 7: Liên bang Nga và Nhật Bản"},
                                {"id": "b8", "title": "Bài 8: Cộng hòa Nhân dân Trung Hoa"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "giao-duc-kinh-te-va-phap-luat-11",
                    "name": "Giáo dục kinh tế và pháp luật 11",
                    "subject_code": "CIVIC",
                    "icon": "⚖️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề 1: Cạnh tranh, Cung cầu, Lạm phát và Thất nghiệp",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Cạnh tranh trong nền kinh tế thị trường"},
                                {"id": "b2", "title": "Bài 2: Cung - cầu trong kinh tế thị trường"},
                                {"id": "b3", "title": "Bài 3: Lạm phát và thất nghiệp"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề 2: Thị trường lao động và Việc làm",
                            "lessons": [
                                {"id": "b4", "title": "Bài 4: Thị trường lao động và việc làm"},
                                {"id": "b5", "title": "Bài 5: Ý tưởng và cơ hội kinh doanh"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chủ đề 3: Quyền bình đẳng của công dân trước pháp luật",
                            "lessons": [
                                {"id": "b6", "title": "Bài 6: Quyền bình đẳng giữa các dân tộc, tôn giáo"},
                                {"id": "b7", "title": "Bài 7: Một số quyền tự do cơ bản của công dân"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tin-hoc-11",
                    "name": "Tin học 11",
                    "subject_code": "INFO",
                    "icon": "💻",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề: Hệ điều hành và Phần mềm ứng dụng",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Hệ điều hành và vai trò của hệ điều hành"},
                                {"id": "b2", "title": "Bài 2: Mạng máy tính và thiết bị mạng"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề: Cơ sở dữ liệu và Ngôn ngữ truy vấn SQL",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Khái niệm cơ sở dữ liệu quan hệ"},
                                {"id": "b4", "title": "Bài 4: Tạo bảng và khóa chính trong SQL"},
                                {"id": "b5", "title": "Bài 5: Truy vấn dữ liệu với lệnh SELECT, WHERE, ORDER BY"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "cong-nghe-11",
                    "name": "Công nghệ 11",
                    "subject_code": "TECH",
                    "icon": "🛠️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Công nghệ Cơ khí chế tạo",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Khái quát về cơ khí chế tạo"},
                                {"id": "b2", "title": "Bài 2: Vật liệu cơ khí mới và phương pháp gia công"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Động cơ đốt trong và Công nghệ Chăn nuôi",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Nguyên lí hoạt động của động cơ đốt trong"},
                                {"id": "b4", "title": "Bài 4: Công nghệ chăn nuôi hiện đại"},
                            ],
                        },
                    ],
                },
            ],
        }
    )

    # =========================================================================
    # LỚP 12
    # =========================================================================
    catalog.append(
        {
            "grade": 12,
            "subjects": [
                {
                    "slug": "toan-12",
                    "name": "Toán 12",
                    "subject_code": "MATH",
                    "icon": "📐",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Ứng dụng đạo hàm để khảo sát và vẽ đồ thị của hàm số",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Tính đơn điệu của hàm số"},
                                {"id": "b2", "title": "Bài 2: Cực trị của hàm số"},
                                {"id": "b3", "title": "Bài 3: Giá trị lớn nhất và giá trị nhỏ nhất của hàm số"},
                                {
                                    "id": "b4",
                                    "title": "Bài 4: Đường tiệm cận của đồ thị hàm số (tiệm cận đứng, ngang, xiên)",
                                },
                                {
                                    "id": "b5",
                                    "title": "Bài 5: Khảo sát sự biến thiên và vẽ đồ thị của hàm số (bậc 3, phân thức)",
                                },
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Vectơ và Hệ tọa độ trong không gian (Oxyz)",
                            "lessons": [
                                {"id": "b6", "title": "Bài 6: Vectơ trong không gian"},
                                {"id": "b7", "title": "Bài 7: Hệ tọa độ trong không gian Oxyz"},
                                {"id": "b8", "title": "Bài 8: Biểu thức tọa độ của các phép toán vectơ, tích vô hướng"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Các số đặc trưng đo mức độ phân tán của mẫu số liệu ghép nhóm",
                            "lessons": [
                                {
                                    "id": "b9",
                                    "title": "Bài 9: Khoảng biến thiên và khoảng tứ phân vị của mẫu số liệu ghép nhóm",
                                },
                                {"id": "b10", "title": "Bài 10: Phương sai và độ lệch chuẩn của mẫu số liệu ghép nhóm"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Nguyên hàm và Tích phân",
                            "lessons": [
                                {"id": "b11", "title": "Bài 11: Nguyên hàm và bảng nguyên hàm cơ bản"},
                                {"id": "b12", "title": "Bài 12: Tích phân và các tính chất"},
                                {
                                    "id": "b13",
                                    "title": "Bài 13: Ứng dụng tích phân tính diện tích hình phẳng và thể tích vật thể tròn xoay",
                                },
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chương 5: Phương pháp tọa độ trong không gian",
                            "lessons": [
                                {"id": "b14", "title": "Bài 14: Phương trình mặt phẳng"},
                                {"id": "b15", "title": "Bài 15: Phương trình đường thẳng trong không gian"},
                                {"id": "b16", "title": "Bài 16: Phương trình mặt cầu"},
                            ],
                        },
                        {
                            "id": "c6",
                            "title": "Chương 6: Xác suất có điều kiện",
                            "lessons": [
                                {"id": "b17", "title": "Bài 17: Xác suất có điều kiện (P(A|B))"},
                                {"id": "b18", "title": "Bài 18: Công thức xác suất toàn phần và công thức Bayes"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "vat-li-12",
                    "name": "Vật lí 12",
                    "subject_code": "PHYSICS",
                    "icon": "⚡",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Vật lí nhiệt",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Cấu trúc của chất, sự chuyển thể"},
                                {
                                    "id": "b2",
                                    "title": "Bài 2: Nội năng và định luật I của nhiệt động lực học (ΔU = A + Q)",
                                },
                                {"id": "b3", "title": "Bài 3: Nhiệt độ, thang nhiệt độ Kelvin"},
                                {
                                    "id": "b4",
                                    "title": "Bài 4: Nhiệt dung riêng, nhiệt nóng chảy riêng và nhiệt hóa hơi riêng",
                                },
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Khí lí tưởng",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Mô hình động học phân tử chất khí"},
                                {
                                    "id": "b6",
                                    "title": "Bài 6: Các định luật chất khí (Boyle, Charles) và Phương trình trạng thái khí lí tưởng (pV = nRT)",
                                },
                                {"id": "b7", "title": "Bài 7: Áp suất khí theo mô hình động học phân tử"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Từ trường",
                            "lessons": [
                                {"id": "b8", "title": "Bài 8: Từ trường và cảm ứng từ B"},
                                {
                                    "id": "b9",
                                    "title": "Bài 9: Lực từ tác dụng lên đoạn dây dẫn mang dòng điện (Lực Lorentz)",
                                },
                                {"id": "b10", "title": "Bài 10: Hiện tượng cảm ứng điện từ và định luật Faraday"},
                                {"id": "b11", "title": "Bài 11: Đại cương về dòng điện xoay chiều"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Vật lí hạt nhân",
                            "lessons": [
                                {"id": "b12", "title": "Bài 12: Cấu tạo hạt nhân và năng lượng liên kết hạt nhân"},
                                {
                                    "id": "b13",
                                    "title": "Bài 13: Hiện tượng phóng xạ (alpha, beta, gamma) và định luật phóng xạ",
                                },
                                {"id": "b14", "title": "Bài 14: Phản ứng hạt nhân, năng lượng phân hạch và nhiệt hạch"},
                                {"id": "b15", "title": "Bài 15: An toàn phóng xạ và ứng dụng y tế, công nghiệp"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "hoa-hoc-12",
                    "name": "Hóa học 12",
                    "subject_code": "CHEMISTRY",
                    "icon": "🧪",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Ester - Lipid - Xà phòng",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Ester (Cấu tạo, danh pháp, tính chất thủy phân)"},
                                {"id": "b2", "title": "Bài 2: Lipid, chất béo và xà phòng, chất giặt rửa tổng hợp"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Carbohydrate",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Glucose và Fructose"},
                                {"id": "b4", "title": "Bài 4: Saccharose và Maltose"},
                                {"id": "b5", "title": "Bài 5: Tinh bột và Cellulose"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Hợp chất chứa nitrogen (Amine - Amino acid - Peptide - Protein)",
                            "lessons": [
                                {"id": "b6", "title": "Bài 6: Amine (Aniline, Methylamine)"},
                                {"id": "b7", "title": "Bài 7: Amino acid (Glycine, Alanine, Lysine, Glutamic acid)"},
                                {"id": "b8", "title": "Bài 8: Peptide, Protein và Enzyme"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Polymer và Vật liệu polymer",
                            "lessons": [
                                {"id": "b9", "title": "Bài 9: Đại cương về polymer (trùng hợp, trùng ngưng)"},
                                {"id": "b10", "title": "Bài 10: Chất dẻo, tơ tổng hợp, cao su và keo dán"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chương 5: Pin điện và Điện phân",
                            "lessons": [
                                {"id": "b11", "title": "Bài 11: Thế điện cực chuẩn của kim loại"},
                                {"id": "b12", "title": "Bài 12: Pin điện hóa Galvanic"},
                                {"id": "b13", "title": "Bài 13: Điện phân dung dịch và điện phân nóng chảy"},
                            ],
                        },
                        {
                            "id": "c6",
                            "title": "Chương 6: Đại cương về kim loại",
                            "lessons": [
                                {"id": "b14", "title": "Bài 14: Vị trí, cấu tạo và tính chất vật lí của kim loại"},
                                {"id": "b15", "title": "Bài 15: Tính chất hóa học của kim loại"},
                                {"id": "b16", "title": "Bài 16: Phương pháp điều chế kim loại và Sự ăn mòn kim loại"},
                            ],
                        },
                        {
                            "id": "c7",
                            "title": "Chương 7: Kim loại chuyển tiếp dãy thứ nhất & Phức chất",
                            "lessons": [
                                {
                                    "id": "b17",
                                    "title": "Bài 17: Nguyên tố kim loại chuyển tiếp dãy thứ nhất (Fe, Cu, Cr...)",
                                },
                                {"id": "b18", "title": "Bài 18: Đại cương về phức chất và ứng dụng"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "sinh-hoc-12",
                    "name": "Sinh học 12",
                    "subject_code": "BIOLOGY",
                    "icon": "🧬",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Di truyền phân tử và Di truyền nhiễm sắc thể",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Gen, mã di truyền và quá trình nhân đôi DNA"},
                                {"id": "b2", "title": "Bài 2: Phiên mã và dịch mã"},
                                {"id": "b3", "title": "Bài 3: Điều hòa hoạt động của gen (Operon Lac)"},
                                {"id": "b4", "title": "Bài 4: Đột biến gen"},
                                {"id": "b5", "title": "Bài 5: Nhiễm sắc thể và đột biến cấu trúc, số lượng NST"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Mở rộng học thuyết di truyền Men-đen",
                            "lessons": [
                                {"id": "b6", "title": "Bài 6: Quy luật phân li và phân li độc lập"},
                                {"id": "b7", "title": "Bài 7: Tương tác gen và tác động đa hiệu của gen"},
                                {"id": "b8", "title": "Bài 8: Liên kết gen và hoán vị gen (Morgan)"},
                                {
                                    "id": "b9",
                                    "title": "Bài 9: Di truyền liên kết với giới tính và di truyền ngoài nhân",
                                },
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Di truyền quần thể và Di truyền người",
                            "lessons": [
                                {
                                    "id": "b10",
                                    "title": "Bài 10: Cấu trúc di truyền của quần thể (Định luật Hardy - Weinberg)",
                                },
                                {"id": "b11", "title": "Bài 11: Di truyền y học và tư vấn di truyền"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Bằng chứng và Cơ chế tiến hóa",
                            "lessons": [
                                {"id": "b12", "title": "Bài 12: Bằng chứng tiến hóa"},
                                {
                                    "id": "b13",
                                    "title": "Bài 13: Thuyết tiến hóa hiện đại tổng hợp (các nhân tố tiến hóa)",
                                },
                                {"id": "b14", "title": "Bài 14: Quá trình hình thành loài mới và nguồn gốc loài người"},
                            ],
                        },
                        {
                            "id": "c5",
                            "title": "Chương 5: Sinh thái học và Môi trường",
                            "lessons": [
                                {"id": "b15", "title": "Bài 15: Môi trường sống và các nhân tố sinh thái"},
                                {"id": "b16", "title": "Bài 16: Quần thể sinh vật"},
                                {"id": "b17", "title": "Bài 17: Quần xã sinh vật và diễn thế sinh thái"},
                                {
                                    "id": "b18",
                                    "title": "Bài 18: Hệ sinh thái, chuỗi thức ăn, lưới thức ăn và chu trình sinh địa hóa",
                                },
                            ],
                        },
                    ],
                },
                {
                    "slug": "ngu-van-12",
                    "name": "Ngữ văn 12",
                    "subject_code": "LIT",
                    "icon": "📖",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Bài 1: Khát vọng tự do và công lí trong văn học hiện đại",
                            "lessons": [
                                {"id": "b1", "title": "Văn bản 1: Tuyên ngôn Độc lập (Hồ Chí Minh)"},
                                {"id": "b2", "title": "Văn bản 2: Tây Tiến (Quang Dũng)"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Bài 2: Vẻ đẹp trữ tình và sử thi trong thơ cách mạng",
                            "lessons": [
                                {"id": "b3", "title": "Văn bản 1: Việt Bắc (Tố Hữu)"},
                                {
                                    "id": "b4",
                                    "title": "Văn bản 2: Đất Nước (Trích Trường ca Mặt đường khát vọng - Nguyễn Khoa Điềm)",
                                },
                                {"id": "b5", "title": "Văn bản 3: Sóng (Xuân Quỳnh)"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Bài 3: Số phận và nhân cách con người trong văn xuôi",
                            "lessons": [
                                {"id": "b6", "title": "Văn bản 1: Rừng xà nu (Nguyễn Trung Thành)"},
                                {"id": "b7", "title": "Văn bản 2: Những đứa con trong gia đình (Nguyễn Thi)"},
                                {"id": "b8", "title": "Văn bản 3: Chiếc thuyền ngoài xa (Nguyễn Minh Châu)"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Bài 4: Kịch hiện đại và xung đột nhân cách",
                            "lessons": [{"id": "b9", "title": "Văn bản 1: Hồn Trương Ba, da hàng thịt (Lưu Quang Vũ)"}],
                        },
                    ],
                },
                {
                    "slug": "tieng-anh-12",
                    "name": "Tiếng Anh 12",
                    "subject_code": "ENG",
                    "icon": "🇬🇧",
                    "chapters": [
                        {
                            "id": "u1",
                            "title": "Unit 1: Life Stories of Famous People",
                            "lessons": [
                                {"id": "l1", "title": "Past Simple vs Past Continuous & Articles"},
                                {"id": "l2", "title": "Biographies and historical achievements"},
                            ],
                        },
                        {
                            "id": "u2",
                            "title": "Unit 2: A Multicultural World",
                            "lessons": [
                                {"id": "l3", "title": "Comparative and superlative structures"},
                                {"id": "l4", "title": "Cultural diversity and global customs"},
                            ],
                        },
                        {
                            "id": "u3",
                            "title": "Unit 3: Green Living",
                            "lessons": [
                                {"id": "l5", "title": "Gerunds and to-infinitives review"},
                                {"id": "l6", "title": "Zero waste and renewable lifestyles"},
                            ],
                        },
                        {
                            "id": "u4",
                            "title": "Unit 4: Urbanisation",
                            "lessons": [
                                {"id": "l7", "title": "Subjunctive mood: It is necessary that S + (should) V"},
                                {"id": "l8", "title": "Urban migration and smart cities"},
                            ],
                        },
                        {
                            "id": "u5",
                            "title": "Unit 5: The World of Work",
                            "lessons": [
                                {"id": "l9", "title": "Phrasal verbs related to careers"},
                                {"id": "l10", "title": "CV writing and job interview skills"},
                            ],
                        },
                        {
                            "id": "u6",
                            "title": "Unit 6: Artificial Intelligence",
                            "lessons": [
                                {"id": "l11", "title": "Active vs Passive Voice in advanced tenses"},
                                {"id": "l12", "title": "AI in healthcare, education and robotics"},
                            ],
                        },
                        {
                            "id": "u7",
                            "title": "Unit 7: Lifelong Learning",
                            "lessons": [
                                {"id": "l13", "title": "Conditional sentence Type 3 & Mixed conditionals"},
                                {"id": "l14", "title": "Continuous professional development"},
                            ],
                        },
                        {
                            "id": "u8",
                            "title": "Unit 8: Choosing a Career",
                            "lessons": [
                                {"id": "l15", "title": "Relative clauses with prepositions"},
                                {"id": "l16", "title": "Future trends in the 4.0 job market"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "lich-su-12",
                    "name": "Lịch sử 12",
                    "subject_code": "HIST",
                    "icon": "🏛️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề 1: Thế giới sau Chiến tranh thế giới thứ hai & ASEAN",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Trật tự hai cực I-an-ta và sự tan rã của Liên Xô"},
                                {
                                    "id": "b2",
                                    "title": "Bài 2: Sự hình thành và phát triển của ASEAN và vị thế của Việt Nam",
                                },
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề 2: Cách mạng tháng Tám và Kháng chiến chống Pháp (1945 - 1954)",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Cách mạng tháng Tám 1945 và thành lập nước VNDCCH"},
                                {"id": "b4", "title": "Bài 4: Kháng chiến chống Pháp và chiến dịch Điện Biên Phủ 1954"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chủ đề 3: Kháng chiến chống Mĩ cứu nước (1954 - 1975)",
                            "lessons": [
                                {
                                    "id": "b5",
                                    "title": "Bài 5: Phong trào Đồng khởi và đánh bại các chiến lược chiến tranh của Mĩ",
                                },
                                {
                                    "id": "b6",
                                    "title": "Bài 6: Cuộc Tổng tiến công và nổi dậy Xuân 1975 và giải phóng miền Nam",
                                },
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chủ đề 4: Công cuộc Đổi mới ở Việt Nam từ 1986 đến nay",
                            "lessons": [
                                {"id": "b7", "title": "Bài 7: Đường lối Đổi mới của Đảng từ Đại hội VI (1986)"},
                                {
                                    "id": "b8",
                                    "title": "Bài 8: Thành tựu kinh tế, xã hội và ngoại giao hội nhập quốc tế",
                                },
                            ],
                        },
                    ],
                },
                {
                    "slug": "dia-li-12",
                    "name": "Địa lí 12",
                    "subject_code": "GEO",
                    "icon": "🌍",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chương 1: Địa lí tự nhiên Việt Nam",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Vị trí địa lí và phạm vi lãnh thổ"},
                                {"id": "b2", "title": "Bài 2: Đất nước nhiều đồi núi"},
                                {"id": "b3", "title": "Bài 3: Thiên nhiên nhiệt đới ẩm gió mùa"},
                                {"id": "b4", "title": "Bài 4: Thiên nhiên phân hóa đa dạng và bảo vệ môi trường"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chương 2: Địa lí dân cư Việt Nam",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Đặc điểm dân số và phân bố dân cư"},
                                {"id": "b6", "title": "Bài 6: Lao động và việc làm, Đô thị hóa"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chương 3: Địa lí các ngành kinh tế Việt Nam",
                            "lessons": [
                                {"id": "b7", "title": "Bài 7: Chuyển dịch cơ cấu kinh tế"},
                                {"id": "b8", "title": "Bài 8: Địa lí nông nghiệp, lâm nghiệp và thủy sản"},
                                {"id": "b9", "title": "Bài 9: Địa lí công nghiệp (năng lượng, chế biến)"},
                                {"id": "b10", "title": "Bài 10: Địa lí các ngành dịch vụ (GTVT, thương mại, du lịch)"},
                            ],
                        },
                        {
                            "id": "c4",
                            "title": "Chương 4: Địa lí các vùng kinh tế Việt Nam",
                            "lessons": [
                                {
                                    "id": "b11",
                                    "title": "Bài 11: Vấn đề phát triển kinh tế ở Trung du và miền núi Bắc Bộ",
                                },
                                {"id": "b12", "title": "Bài 12: Chuyển dịch cơ cấu kinh tế ở Đồng bằng sông Hồng"},
                                {
                                    "id": "b13",
                                    "title": "Bài 13: Phát triển kinh tế - xã hội ở Bắc Trung Bộ và Duyên hải Nam Trung Bộ",
                                },
                                {"id": "b14", "title": "Bài 14: Khai thác thế mạnh ở Tây Nguyên"},
                                {"id": "b15", "title": "Bài 15: Khai thác lãnh thổ theo chiều sâu ở Đông Nam Bộ"},
                                {
                                    "id": "b16",
                                    "title": "Bài 16: Sử dụng hợp lí và cải tạo tự nhiên ở Đồng bằng sông Cửu Long",
                                },
                                {
                                    "id": "b17",
                                    "title": "Bài 17: Phát triển kinh tế biển đảo và bảo vệ chủ quyền quốc gia",
                                },
                            ],
                        },
                    ],
                },
                {
                    "slug": "giao-duc-kinh-te-va-phap-luat-12",
                    "name": "Giáo dục kinh tế và pháp luật 12",
                    "subject_code": "CIVIC",
                    "icon": "⚖️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề 1: Tăng trưởng, phát triển kinh tế và hội nhập quốc tế",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Tăng trưởng và phát triển kinh tế bền vững"},
                                {"id": "b2", "title": "Bài 2: Hội nhập kinh tế quốc tế của Việt Nam"},
                                {"id": "b3", "title": "Bài 3: Bảo hiểm và an sinh xã hội"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề 2: Quản lí thu chi và lập kế hoạch tài chính",
                            "lessons": [
                                {"id": "b4", "title": "Bài 4: Quản lí thu chi trong gia đình"},
                                {"id": "b5", "title": "Bài 5: Lập kế hoạch tài chính cá nhân"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chủ đề 3: Quyền và nghĩa vụ của công dân trong đời sống",
                            "lessons": [
                                {"id": "b6", "title": "Bài 6: Quyền và nghĩa vụ công dân về bầu cử, ứng cử"},
                                {"id": "b7", "title": "Bài 7: Quyền khiếu nại, tố cáo và phòng chống tham nhũng"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "tin-hoc-12",
                    "name": "Tin học 12",
                    "subject_code": "INFO",
                    "icon": "💻",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Chủ đề 1: Trí tuệ nhân tạo (AI) và Cách mạng công nghiệp 4.0",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Giới thiệu về Trí tuệ nhân tạo (AI)"},
                                {"id": "b2", "title": "Bài 2: Ứng dụng AI và đạo đức trong kỉ nguyên số"},
                            ],
                        },
                        {
                            "id": "c2",
                            "title": "Chủ đề 2: Mạng máy tính và Truyền thông",
                            "lessons": [
                                {"id": "b3", "title": "Bài 3: Giao thức mạng TCP/IP và địa chỉ IP"},
                                {"id": "b4", "title": "Bài 4: Bảo mật mạng và an toàn dữ liệu"},
                            ],
                        },
                        {
                            "id": "c3",
                            "title": "Chủ đề 3: Thiết kế trang web với HTML và CSS",
                            "lessons": [
                                {"id": "b5", "title": "Bài 5: Các thẻ HTML cơ bản và cấu trúc trang web"},
                                {"id": "b6", "title": "Bài 6: Định kiểu trang web với CSS"},
                                {"id": "b7", "title": "Bài 7: Xuất bản và quản trị website"},
                            ],
                        },
                    ],
                },
                {
                    "slug": "cong-nghe-12",
                    "name": "Công nghệ 12",
                    "subject_code": "TECH",
                    "icon": "🛠️",
                    "chapters": [
                        {
                            "id": "c1",
                            "title": "Công nghệ Điện - Điện tử",
                            "lessons": [
                                {"id": "b1", "title": "Bài 1: Khái quát về kĩ thuật điện và linh kiện điện tử"},
                                {
                                    "id": "b2",
                                    "title": "Bài 2: Mạch điện tử điều khiển và Vi điều khiển (Arduino/Microcontroller)",
                                },
                                {"id": "b3", "title": "Bài 3: Hệ thống điện quốc gia và lưới điện thông minh"},
                            ],
                        },
                    ],
                },
            ],
        }
    )

    return catalog
