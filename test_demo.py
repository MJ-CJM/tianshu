"""Demo test file to show testing capabilities"""

def test_addition():
    """Test basic addition"""
    assert 1 + 1 == 2

def test_subtraction():
    """Test basic subtraction"""
    assert 5 - 3 == 2

def test_multiplication():
    """Test basic multiplication"""
    assert 2 * 3 == 6

def test_division():
    """Test basic division"""
    assert 10 / 2 == 5

def test_string_concatenation():
    """Test string operations"""
    assert "Hello" + " " + "World" == "Hello World"

def test_list_operations():
    """Test list operations"""
    numbers = [1, 2, 3, 4, 5]
    assert len(numbers) == 5
    assert sum(numbers) == 15
    assert numbers[0] == 1
    assert numbers[-1] == 5

def test_dictionary_operations():
    """Test dictionary operations"""
    person = {"name": "Alice", "age": 30, "city": "New York"}
    assert person["name"] == "Alice"
    assert person["age"] == 30
    assert "city" in person
    assert len(person) == 3

class TestMathOperations:
    """Test class for math operations"""
    
    def test_power(self):
        """Test power operation"""
        assert 2 ** 3 == 8
        assert 5 ** 2 == 25
    
    def test_modulo(self):
        """Test modulo operation"""
        assert 10 % 3 == 1
        assert 15 % 5 == 0
    
    def test_floor_division(self):
        """Test floor division"""
        assert 10 // 3 == 3
        assert 20 // 6 == 3