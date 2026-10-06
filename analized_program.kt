fun gradeOf(score: Int): String {
    var grade = ""                                    
    if (score >= 90) {                                
        grade = "A"                                   
    } else if (score >= 75) {                         
        grade = "B"                                   
    } else if (score >= 60) {                        
        grade = "C"                                   
    } else {
        grade = "D"                               
    }
    return grade                                  
}

fun bonusFor(grade: String): Int {
    var bonus = 0                                    
    when (grade) {
        "A" -> bonus = 20                             
        "B" -> bonus = 10                            
        "C" -> bonus = 5                              
        else -> bonus = 0                             
    }
    return bonus                                    
}

fun countAbove(scores: IntArray, limit: Int): Int {
    var count = 0                                    
    for (i in 0 until scores.size) {                 
        if (scores[i] > limit) {                      
            count++                                   
        }
    }
    return count                                     
}

fun digitSum(number: Int): Int {
    var rest = number                                
    var sum = 0                                      
    while (rest > 0) {                                
        sum += rest % 10                              
        rest /= 10                                    
    }
    return sum                                        
}

fun stepsToZero(start: Int): Int {
    var x = start                                    
    var steps = 0                                     
    do {
        if (x % 2 == 0) {                            
            x /= 2                                    
        } else {
            x -= 1                                    
        }
        steps++                                      
    } while (x > 0)                                  
    return steps                                    
}

fun main() {
    val scores = intArrayOf(95, 82, 67, 55, 45)       
    var total = 0                                     
    var passed = 0                                   

    for (i in 0 until scores.size) {                  
        val grade = gradeOf(scores[i])               
        val bonus = bonusFor(grade)                  
        if (scores[i] >= 60) {                        
            passed++                                 
            when (grade) {
                "A" -> total += bonus                
                "B" -> {                             
                    var attempt = 0                   
                    while (attempt < 2) {             
                        total += digitSum(scores[i])  
                        attempt++                     
                    }
                }
                else -> total += 1                    
            }
        } else if (scores[i] >= 50) {               
            total += stepsToZero(scores[i])          
        } else {
            total -= 1                               
        }
    }

    val above = countAbove(scores, 70)               
    if (above > passed / 2) {                         
        println("Большинство выше 70")               
    } else {
        println("Меньшинство выше 70")              
    }
    println(total)                                   
}