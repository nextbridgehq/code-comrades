package fixture

func readConfigChecked(path string) ([]byte, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return data, nil
}

func readConfigUnchecked(path string) []byte {
	data, _ := os.ReadFile(path)
	return data
}
