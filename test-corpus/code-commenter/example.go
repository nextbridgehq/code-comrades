package fixture

func GetName(u *User) string {
	return u.Name
}

func Double(x int) int {
	return x * 2
}

func RetryWithBackoff(fn func() error, maxAttempts int) error {
	var err error
	for attempt := 0; attempt < maxAttempts; attempt++ {
		err = fn()
		if err == nil {
			return nil
		}
		time.Sleep(time.Duration(attempt*attempt) * time.Second)
	}
	return err
}

func Deduplicate(items []string) []string {
	seen := make(map[string]bool)
	result := make([]string, 0, len(items))
	for _, item := range items {
		if !seen[item] {
			seen[item] = true
			result = append(result, item)
		}
	}
	return result
}

func IsEmpty(s string) bool {
	return len(s) == 0
}
